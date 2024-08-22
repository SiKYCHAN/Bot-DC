import discord
import yt_dlp as youtube_dl
import asyncio
import subprocess
import logging
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from urllib.parse import urlparse
import json
from myserver import server_on

# ฟังก์ชันสำหรับตรวจสอบการติดตั้ง FFmpeg
def test_ffmpeg():
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        if result.returncode == 0:
            print("FFmpeg is installed and working.")
            print(result.stdout)
        else:
            print("FFmpeg is not working correctly.")
    except FileNotFoundError:
        print("FFmpeg not found")

# ทดสอบการติดตั้ง FFmpeg
test_ffmpeg()

# ตั้งค่า intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

# ตั้งค่า Spotify API
spotify_client_id = '283ba8b3d2494be4a4a79ea2dae08474'
spotify_client_secret = 'f67e7eb7911e465bb1edccb1c1c4cf24'

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=spotify_client_id,
                                                           client_secret=spotify_client_secret))

# ตั้งค่าบอท
bot = discord.Client(intents=intents)

# ฟังก์ชันสำหรับบันทึกและโหลด TARGET_CHANNEL_ID
def load_target_channel_id():
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            return config.get('target_channel_id', None)
    except FileNotFoundError:
        return None

def save_target_channel_id(channel_id):
    with open('config.json', 'w') as f:
        json.dump({'target_channel_id': channel_id}, f)

# โหลด target_channel_id เมื่อบอทเริ่มทำงาน
target_channel_id = load_target_channel_id()

# ฟังก์ชันสำหรับแปลง Spotify URL เป็น YouTube URL
def get_youtube_url_from_spotify(spotify_url):
    try:
        parsed_url = urlparse(spotify_url)
        if parsed_url.netloc not in ['open.spotify.com', 'api.spotify.com']:
            raise ValueError("Invalid Spotify URL")
        
        track_info = sp.track(spotify_url)
        track_name = track_info['name']
        track_artists = ', '.join([artist['name'] for artist in track_info['artists']])
        search_query = f"{track_name} {track_artists}"

        with youtube_dl.YoutubeDL({'quiet': True}) as ydl:
            result = ydl.extract_info(f"ytsearch:{search_query}", download=False)
            if 'entries' in result:
                return result['entries'][0]['webpage_url']
            return None
    except Exception as e:
        print(f"Error finding YouTube URL: {e}")
        return None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')  # แสดงชื่อบอทเมื่อบอทออนไลน์

# โหลด yt-dlp
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'default_search': 'ytsearch',
        }
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            try:
                data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                if 'entries' in data:
                    data = data['entries'][0]
                filename = data['url']
                return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
            except Exception as e:
                print(f"Error extracting info from URL: {e}")
                return None

@bot.event
async def on_message(message):
    global target_channel_id

    if message.author == bot.user:
        return

    # คำสั่งเพื่อกำหนดหรือแสดง TARGET_CHANNEL_ID
    if message.content.startswith('!set_channel '):
        try:
            target_channel_id = int(message.content.split(' ')[1])
            save_target_channel_id(target_channel_id)  # บันทึก channel ID
            await message.channel.send(f"Target channel ID set to {target_channel_id}.")
        except ValueError:
            await message.channel.send("Invalid channel ID format. Please provide a valid numeric channel ID.")
        return

    if message.content.startswith('!get_channel'):
        if target_channel_id:
            await message.channel.send(f"Current target channel ID is {target_channel_id}.")
        else:
            await message.channel.send("No target channel ID set.")
        return

    # Check if the message is in the target channel
    if target_channel_id is None or message.channel.id != target_channel_id:
        return

    query = message.content.strip()

    if message.author.voice and not message.guild.voice_client:
        channel = message.author.voice.channel
        await channel.connect()

    async with message.channel.typing():
        if "spotify" in query:
            youtube_url = get_youtube_url_from_spotify(query)
            if youtube_url is None:
                await message.channel.send("ไม่สามารถค้นหาวิดีโอใน YouTube ได้")
                return
        else:
            youtube_url = f"ytsearch:{query}"

        try:
            player = await YTDLSource.from_url(youtube_url, loop=bot.loop, stream=True)
            if player is None:
                await message.channel.send("ไม่พบเพลงที่ต้องการ")
                return

            if message.guild.voice_client.is_playing():
                message.guild.voice_client.stop()

            message.guild.voice_client.play(player, after=lambda e: bot.loop.create_task(check_end(message) if not e else bot.loop.create_task(retry_play(message, youtube_url))))
            await message.channel.send(f'กำลังเล่น: {player.title}')
        except Exception as e:
            await message.channel.send(f"เกิดข้อผิดพลาดในการเล่นเพลง: {e}")
            print(f"Error playing song: {e}")

async def check_end(message):
    while message.guild.voice_client.is_playing():
        await asyncio.sleep(1)
    await asyncio.sleep(5)
    await message.guild.voice_client.disconnect()

async def retry_play(message, url):
    await asyncio.sleep(5)
    await on_message(message)  # Call on_message to retry playing

# ตั้งค่าการบันทึกข้อผิดพลาด
logging.basicConfig(level=logging.ERROR)

# ใช้ Token ของบอทที่คุณได้รับจาก Discord Developer Portal
bot.run('MTI3NTE0OTA0NjA0NTk5OTE5Nw.GzyUBI.kKI1tRwKvKlZVEdxIhxT475Z5-uyAnQx10OC-M')
