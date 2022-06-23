import shutil
import time
from math import ceil
from re import match as re_match, findall as re_findall
from html import escape
from threading import Event, Thread
from urllib.request import urlopen

import psutil
from psutil import cpu_percent, disk_usage, virtual_memory
from requests import head as rhead
from telegram import InlineKeyboardMarkup
from telegram.error import RetryAfter
from telegram.ext import CallbackQueryHandler
from telegram.message import Message
from telegram.update import Update

from bot import *
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker


MAGNET_REGEX = r"magnet:\?xt=urn:btih:[a-zA-Z0-9]*"

URL_REGEX = r"(?:(?:https?|ftp):\/\/)?[\w/\-?=%.]+\.[\w/\-?=%.]+"

COUNT = 0
PAGE_NO = 1


class MirrorStatus:
    STATUS_UPLOADING = "Uᴘʟᴏᴀᴅɪɴɢ...📤"
    STATUS_DOWNLOADING = "Dᴏᴡɴʟᴏᴀᴅɪɴɢ...📥"
    STATUS_CLONING = "Cʟᴏɴɪɴɢ...♻️"
    STATUS_WAITING = "Qᴜᴇᴜᴇᴅ...💤"
    STATUS_FAILED = "Fᴀɪʟᴇᴅ 🚫. Cʟᴇᴀɴɪɴɢ Dᴏᴡɴʟᴏᴀᴅ..."
    STATUS_PAUSE = "Pᴀᴜsᴇᴅ...⛔️"
    STATUS_ARCHIVING = "Aʀᴄʜɪᴠɪɴɢ...🔐"
    STATUS_EXTRACTING = "Exᴛʀᴀᴄᴛɪɴɢ...📂"
    STATUS_SPLITTING = "Sᴘʟɪᴛᴛɪɴɢ...✂️"
    STATUS_CHECKING = "CʜᴇᴄᴋɪɴɢUᴘ...📝"
    STATUS_SEEDING = "Sᴇᴇᴅɪɴɢ...🌧"
    
    
PROGRESS_MAX_SIZE = 100 // 10
PROGRESS_INCOMPLETE = ['◔', '◔', '◑', '◑', '◑', '◕', '◕']

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']


class setInterval:
    def __init__(self, interval, action):
        self.interval = interval
        self.action = action
        self.stopEvent = Event()
        thread = Thread(target=self.__setInterval)
        thread.start()

    def __setInterval(self):
        nextTime = time() + self.interval
        while not self.stopEvent.wait(nextTime - time()):
            nextTime += self.interval
            self.action()

    def cancel(self):
        self.stopEvent.set()

def get_readable_file_size(size_in_bytes) -> str:
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024:
        size_in_bytes /= 1024
        index += 1
    try:
        return f'{round(size_in_bytes, 2)}{SIZE_UNITS[index]}'
    except IndexError:
        return 'File too large'

def getDownloadByGid(gid):
    with download_dict_lock:
        for dl in list(download_dict.values()):
            status = dl.status()
            if (
                status
                not in [
                    MirrorStatus.STATUS_ARCHIVING,
                    MirrorStatus.STATUS_EXTRACTING,
                    MirrorStatus.STATUS_SPLITTING,
                ]
                and dl.gid() == gid
            ):
                return dl
    return None

def getAllDownload(req_status: str):
    with download_dict_lock:
        for dl in list(download_dict.values()):
            status = dl.status()
            if status not in [MirrorStatus.STATUS_ARCHIVING, MirrorStatus.STATUS_EXTRACTING, MirrorStatus.STATUS_SPLITTING] and dl:
                if req_status == 'down' and (status not in [MirrorStatus.STATUS_SEEDING,
                                                            MirrorStatus.STATUS_UPLOADING,
                                                            MirrorStatus.STATUS_CLONING]):
                    return dl
                elif req_status == 'up' and status == MirrorStatus.STATUS_UPLOADING:
                    return dl
                elif req_status == 'clone' and status == MirrorStatus.STATUS_CLONING:
                    return dl
                elif req_status == 'seed' and status == MirrorStatus.STATUS_SEEDING:
                    return dl
                elif req_status == 'all':
                    return dl
    return None

def get_progress_bar_string(status):
    completed = status.processed_bytes() / 8
    total = status.size_raw() / 8
    p = 0 if total == 0 else round(completed * 100 / total)
    p = min(max(p, 0), 100)
    cFull = p // 8
    p_str = '⬤' * cFull
    p_str += '○' * (12 - cFull)
    p_str = f"[{p_str}]"
    return p_str

def get_readable_message():
    with download_dict_lock:
        msg = ""
        if STATUS_LIMIT is not None:
            tasks = len(download_dict)
            global pages
            pages = ceil(tasks/STATUS_LIMIT)
            if PAGE_NO > pages and pages != 0:
                globals()['COUNT'] -= STATUS_LIMIT
                globals()['PAGE_NO'] -= 1
        for index, download in enumerate(list(download_dict.values())[COUNT:], start=1):
            
            msg += "\nMirroring Under Process! Please Wait...\n"
            msg += "\n「𝗔𝗦𝗜 𝗠𝗜𝗥𝗥𝗢𝗥 𝗕𝗢𝗧𝗦」\n\n"
            msg += f"<b>╭─Fɪʟᴇ Nᴀᴍᴇ:</b> <code>{escape(str(download.name()))}</code>"
            msg += f"\n<b>├⌬Sᴛᴀᴛᴜs:</b> <i>{download.status()}</i>"
            
            # msg += f"<b>├⌬Nᴀᴍᴇ:</b> <code>{escape(str(download.name()))}</code>"
            # msg += f"\n<b>├⌬Sᴛᴀᴛᴜs:</b> <i>{download.status()}</i>"
            if download.status() not in [
                MirrorStatus.STATUS_ARCHIVING,
                MirrorStatus.STATUS_EXTRACTING,
                MirrorStatus.STATUS_SPLITTING,
                MirrorStatus.STATUS_SEEDING,
            ]:
                msg += f"\n{get_progress_bar_string(download)} {download.progress()}"
                if download.status() == MirrorStatus.STATUS_CLONING:
                    msg += f"\n<b>├⌬Cʟᴏɴᴇᴅ:</b> {get_readable_file_size(download.processed_bytes())} of {download.size()}"
                elif download.status() == MirrorStatus.STATUS_UPLOADING:
                    msg += f"\n<b>├⌬Uᴘʟᴏᴀᴅᴇᴅ:</b> {get_readable_file_size(download.processed_bytes())} of {download.size()}"
                else:
                    msg += f"\n<b>├⌬Dᴏᴡɴʟᴏᴀᴅᴇᴅ:</b> {get_readable_file_size(download.processed_bytes())} of {download.size()}"
                msg += f"\n<b>├⌬Sᴘᴇᴇᴅ:</b> {download.speed()} | <b>ETA:</b> {download.eta()}"
                try:
                    msg += f"\n<b>├⌬Sᴇᴇᴅᴇʀs:</b> {download.aria_download().num_seeders}" \
                           f" | <b>├⌬Pᴇᴇʀs:</b> {download.aria_download().connections}"
                    msg += f"\n<b>├⌬Eɴɢɪɴᴇ:</b> <code>Aria2c v1.35.0</code>"
                except:
                    pass
                try:
                    msg += f"\n<b>├⌬Sᴇᴇᴅᴇʀs:</b> {download.torrent_info().num_seeds}" \
                           f" | <b>├⌬Lᴇᴇᴄʜᴇʀs:</b> {download.torrent_info().num_leechs}"
                    msg += f"\n<b>├⌬Eɴɢɪɴᴇ:</b> <code>qBittorrent v4.4.2</code>"
                except:
                    pass
                msg += f'\n<b>├⌬Rᴇᴏ̨ᴜᴇsᴛᴇᴅ Bʏ:</b> ️<code>{download.message.from_user.first_name}</code>️(<code>{download.message.from_user.id}</code>)'
                chatid = str(download.message.chat.id)[4:]
                msg += f'\n<b>├⌬Sᴏᴜʀᴄᴇ Msɢ Bʏ Usᴇʀ: </b><a href="https://t.me/c/{chatid}/{download.message.message_id}">Link</a>'
                msg += f"\n<b>├⌬Eʟᴀᴘsᴇᴅ: </b>{get_readable_time(time() - download.message.date.timestamp())}"
                msg += f"\n<b>╰─Tᴏ Cᴀɴᴄᴇʟ:</b> <code>/{BotCommands.CancelMirror} {download.gid()}</code>"
            elif download.status() == MirrorStatus.STATUS_SEEDING:
                msg += f"\n<b>├⌬Sɪᴢᴇ: </b>{download.size()}"
                msg += f"\n<b>├⌬Eɴɢɪɴᴇ:</b> <code>qBittorrent v4.4.2</code>"
                msg += f"\n<b>├⌬Sᴘᴇᴇᴅ: </b>{get_readable_file_size(download.torrent_info().upspeed)}/s"
                msg += f" | <b>├⌬Uᴘʟᴏᴀᴅᴇᴅ: </b>{get_readable_file_size(download.torrent_info().uploaded)}"
                msg += f"\n<b>├⌬Rᴀᴛɪᴏ: </b>{round(download.torrent_info().ratio, 3)}"
                msg += f" | <b>├⌬Tɪᴍᴇ: </b>{get_readable_time(download.torrent_info().seeding_time)}"
                msg += f"\n<b>├⌬Rᴇᴏ̨ᴜᴇsᴛᴇᴅ Bʏ:</b> ️<code>{download.message.from_user.first_name}</code>️"
                msg += f"\n<b>├⌬Eʟᴀᴘsᴇᴅ: </b>{get_readable_time(time() - download.message.date.timestamp())}"
                msg += f"\n<b>╰─Tᴏ Cᴀɴᴄᴇʟ:</b> <code>/{BotCommands.CancelMirror} {download.gid()}</code>"
            else:
                msg += f"\n<b>├⌬Sɪᴢᴇ: </b>{download.size()}"
            msg += "\n\n"
            if STATUS_LIMIT is not None and index == STATUS_LIMIT:
                break
                
        bmsg = f"<b>「 <i>Performance Meter</i> 」</b>"
        bmsg += f"\n<b>├⌬Cᴘᴜ        :</b> {cpu_percent()}%\n<b>├⌬Ssᴅ        :</b> {get_readable_file_size(disk_usage(DOWNLOAD_DIR).free)}"
        bmsg += f"\n<b>├⌬Rᴀᴍ       :</b> {virtual_memory().percent}%\n<b>├⌬Uᴘᴛɪᴍᴇ  :</b> {get_readable_time(time() - botStartTime)}"

        # bmsg = f"<b>├⌬Cᴘᴜ:</b> {cpu_percent()}% | <b>├⌬Fʀᴇᴇ :</b> {get_readable_file_size(disk_usage(DOWNLOAD_DIR).free)}"
        # bmsg += f"\n<b>├⌬Rᴀᴍ:</b> {virtual_memory().percent}% | <b>├⌬Uᴘᴛɪᴍᴇ:</b> {get_readable_time(time() - botStartTime)}"
        
        dlspeed_bytes = 0
        upspeed_bytes = 0
        for download in list(download_dict.values()):
            spd = download.speed()
            if download.status() == MirrorStatus.STATUS_DOWNLOADING:
                if 'K' in spd:
                    dlspeed_bytes += float(spd.split('K')[0]) * 1024
                elif 'M' in spd:
                    dlspeed_bytes += float(spd.split('M')[0]) * 1048576
            elif download.status() == MirrorStatus.STATUS_UPLOADING:
                if 'KB/s' in spd:
                    upspeed_bytes += float(spd.split('K')[0]) * 1024
                elif 'MB/s' in spd:
                    upspeed_bytes += float(spd.split('M')[0]) * 1048576
                    
        bmsg += f"\n\n<b>「 <i>Sᴘᴇᴇᴅᴏᴍᴇᴛᴇʀ</i> 」</b>\n<b>Dᴏᴡɴʟᴏᴀᴅ :</b> {get_readable_file_size(dlspeed_bytes)}/s\n<b>Uᴘʟᴏᴀᴅ      :</b> {get_readable_file_size(upspeed_bytes)}/s"
        buttons = ButtonMaker()
        buttons.sbutton("Statistics", str(THREE))
        sbutton = InlineKeyboardMarkup(buttons.build_menu(1))
        # bmsg += f"\n<b>DL:</b> {get_readable_file_size(dlspeed_bytes)}/s | <b>UL:</b> {get_readable_file_size(upspeed_bytes)}/s"
        if STATUS_LIMIT is not None and tasks > STATUS_LIMIT:
            msg += f"<b>Pᴀɢᴇ:</b> {PAGE_NO}/{pages} | <b>Tᴀsᴋs:</b> {tasks}\n"
            buttons = ButtonMaker()
            buttons.sbutton("Prev", "status pre")
            buttons.sbutton("Stats", str(THREE))
            buttons.sbutton("Next", "status nex")
            button = InlineKeyboardMarkup(buttons.build_menu(3))
            return msg + bmsg, button
        return msg + bmsg, sbutton

def turn(data):
    try:
        with download_dict_lock:
            global COUNT, PAGE_NO
            if data[1] == "nex":
                if PAGE_NO == pages:
                    COUNT = 0
                    PAGE_NO = 1
                else:
                    COUNT += STATUS_LIMIT
                    PAGE_NO += 1
            elif data[1] == "pre":
                if PAGE_NO == 1:
                    COUNT = STATUS_LIMIT * (pages - 1)
                    PAGE_NO = pages
                else:
                    COUNT -= STATUS_LIMIT
                    PAGE_NO -= 1
        return True
    except:
        return False

def get_readable_time(seconds: int) -> str:
    result = ''
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f'{days}d'
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f'{hours}h'
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f'{minutes}m'
    seconds = int(seconds)
    result += f'{seconds}s'
    return result

def is_url(url: str):
    url = re_findall(URL_REGEX, url)
    return bool(url)

def is_gdrive_link(url: str):
    return "drive.google.com" in url

def is_gdtot_link(url: str):
    url = re_match(r'https?://.+\.gdtot\.\S+', url)
    return bool(url)

def is_mega_link(url: str):
    return "mega.nz" in url or "mega.co.nz" in url

def get_mega_link_type(url: str):
    if "folder" in url:
        return "folder"
    elif "file" in url:
        return "file"
    elif "/#F!" in url:
        return "folder"
    return "file"

def is_magnet(url: str):
    magnet = re_findall(MAGNET_REGEX, url)
    return bool(magnet)

def new_thread(fn):
    """To use as decorator to make a function call threaded.
    Needs import
    from threading import Thread"""

    def wrapper(*args, **kwargs):
        thread = Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread

    return wrapper

def get_content_type(link: str) -> str:
    try:
        res = rhead(link, allow_redirects=True, timeout=5, headers = {'user-agent': 'Wget/1.12'})
        content_type = res.headers.get('content-type')
    except:
        try:
            res = urlopen(link, timeout=5)
            info = res.info()
            content_type = info.get_content_type()
        except:
            content_type = None
    return content_type

ONE, TWO, THREE = range(3)

def pop_up_stats(update, context):
    query = update.callback_query
    stats = bot_sys_stats()
    query.answer(text=stats, show_alert=True)

def bot_sys_stats():
    currentTime = get_readable_time(time() - botStartTime)
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage(DOWNLOAD_DIR).percent
    total, used, free = shutil.disk_usage(DOWNLOAD_DIR)
    total = get_readable_file_size(total)
    used = get_readable_file_size(used)
    free = get_readable_file_size(free)
    recv = get_readable_file_size(psutil.net_io_counters().bytes_recv)
    sent = get_readable_file_size(psutil.net_io_counters().bytes_sent)
    num_active = 0
    num_upload = 0
    num_split = 0
    num_extract = 0
    num_archi = 0
    tasks = len(download_dict)
    
    for stats in list(download_dict.values()):
       if stats.status() == MirrorStatus.STATUS_DOWNLOADING:
                num_active += 1
       if stats.status() == MirrorStatus.STATUS_UPLOADING:
                num_upload += 1
       if stats.status() == MirrorStatus.STATUS_ARCHIVING:
                num_archi += 1
       if stats.status() == MirrorStatus.STATUS_EXTRACTING:
                num_extract += 1
       if stats.status() == MirrorStatus.STATUS_SPLITTING:
                num_split += 1

    stats = f"𝔸𝕊𝕀 𝔹𝕆𝕋 𝕊𝕥𝕒𝕥𝕤"
    stats += f"""

\nBot Uptime 🕐: {currentTime}\n
CPU: {cpu}%
RAM: {mem}%
\nDISK: {disk}%\n
TOTAL: {total}
USED: {used} | FREE: {free}
\nSENT: {sent} | RECV: {recv}\n

BY : 𝑀𝒜𝐻𝐼𝒯𝐻 [𝘼𝙎𝙄 𝙈𝙞𝙧𝙧𝙤𝙧 𝘽𝙤𝙩]
"""

    return stats
dispatcher.add_handler(
    CallbackQueryHandler(pop_up_stats, pattern="^" + str(THREE) + "$")
)
