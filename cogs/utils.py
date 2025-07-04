import discord
import pytz
import re
import calendar
import random
import string
import os
import boto3
import concurrent.futures
import asyncio
from botocore.config import Config
from discord.ext import commands
from discord import app_commands
from typing import Optional, List
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote

class TimezoneTransformer(app_commands.Transformer):
    async def transform(self, interaction, value:str) -> str:
        return value
    
    async def autocomplete(self, interaction, current:str):
        utils_cog = interaction.client.get_cog("UtilsCog")
        if not utils_cog:
            return []
        return [app_commands.Choice(name=tz, value=tz) 
                for tz in utils_cog.COMMON_TIMEZONES 
                if current.lower() in tz.lower()][:25]  # Discord limit

class UtilsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Environment variables
        self.R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
        self.R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
        self.R2_ENDPOINT = os.getenv("R2_ENDPOINT")
        self.R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
        self.MAX_DISCORD_FILE_SIZE = 10 * 1024 * 1024
        self.CHUNK_SIZE = 10 * 1024 * 1024
        
        # Common timezones list
        self.COMMON_TIMEZONES = [
            "UTC",
            "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Alaska", "US/Hawaii",
            "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
            "America/Toronto", "America/Vancouver", "America/Mexico_City",
            "America/Sao_Paulo", "America/Buenos_Aires", "America/Santiago", "America/Bogota",
            "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome",
            "Europe/Moscow", "Europe/Athens", "Europe/Dublin", "Europe/Amsterdam", "Europe/Stockholm",
            "Europe/Vienna", "Europe/Warsaw", "Europe/Istanbul", "Europe/Kiev", "Europe/Lisbon",
            "Asia/Tokyo", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Singapore", "Asia/Seoul",
            "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Jakarta", "Asia/Manila",
            "Asia/Taipei", "Asia/Jerusalem", "Asia/Riyadh", "Asia/Baghdad",
            "Australia/Sydney", "Australia/Melbourne", "Australia/Perth", "Australia/Brisbane",
            "Pacific/Auckland", "Pacific/Fiji", "Pacific/Honolulu",
            "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos", "Africa/Nairobi", "Africa/Casablanca"
        ]
    
    async def initialize_relationships(self):
        # This cog doesn't need references to others
        pass
    
    def sanitize_filename(self, name: str) -> str:
        name = re.sub(r'[\\/*?:"<>|]', '_', name)
        return name.replace(' ', '-')
    
    async def upload_to_cdn(self, file_path, guild_id):
        return await self.upload_to_cloudflare_r2(file_path, guild_id)
    
    async def upload_to_cloudflare_r2(self, file_path, guild_id):
        unique_id = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        def upload_to_r2():
            s3 = boto3.client(
                's3',
                endpoint_url=self.R2_ENDPOINT,
                aws_access_key_id=self.R2_ACCESS_KEY,
                aws_secret_access_key=self.R2_SECRET_KEY,
                config=Config(signature_version='s3v4')
            )
            server_folder = str(guild_id)
            original_filename = os.path.basename(file_path)
            storage_key = f"backup/{server_folder}/{unique_id}/{original_filename}"
            s3.upload_file(file_path, self.R2_BUCKET_NAME, storage_key)
            encoded_filename = quote(original_filename)
            return f"https://cdn.backupbot.net/backup/{server_folder}/{unique_id}/{encoded_filename}"
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return await asyncio.get_event_loop().run_in_executor(pool, upload_to_r2)
    
    def split_file(self, path, chunk_size=None):
        if chunk_size is None:
            chunk_size = self.CHUNK_SIZE
        size = os.path.getsize(path)
        base = os.path.splitext(path)[0]
        chunks = []
        with open(path,'rb') as f:
            idx = 0
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                part = f"{base}_part{idx:03d}.zip"
                with open(part,'wb') as pf:
                    pf.write(data)
                chunks.append(part)
                idx += 1
        return chunks
    
    async def send_chunked_backup(self, channel, name, zip_name):
        chunks = self.split_file(zip_name)
        total = len(chunks)
        if total == 0:
            await channel.send("Error: Failed to create backup chunks.")
            return False
        await channel.send(f"Server backup for {name} (Total parts: {total}):")
        try:
            for i, part in enumerate(chunks):
                with open(part,'rb') as f:
                    await channel.send(f"Backup part {i+1}/{total}:", file=discord.File(f, filename=os.path.basename(part)))
                os.remove(part)
            return True
        except:
            for c in chunks:
                if os.path.exists(c):
                    os.remove(c)
            return False
    
    def calculate_next_run(self, tz_str, freq):
        tz = pytz.timezone(tz_str)
        now = datetime.now(tz=tz)
        if freq=="hourly":
            return now+timedelta(hours=1)
        if freq=="daily":
            return now+timedelta(days=1)
        if freq=="weekly":
            return now+timedelta(days=7)
        if freq=="monthly":
            ny = now.year+(1 if now.month==12 else 0)
            nm = 1 if now.month==12 else now.month+1
            ld = calendar.monthrange(ny,nm)[1]
            day = min(now.day, ld)
            return now.replace(year=ny, month=nm, day=day)
        if freq=="yearly":
            return now.replace(year=now.year+1)
        return now+timedelta(days=1)
    
    @commands.hybrid_command(name="ping", description="Test the bot's response time")
    async def ping(self, ctx):
        await ctx.send(f"Pong! `{round(self.bot.latency*1000)}ms`")

async def setup(bot):
    await bot.add_cog(UtilsCog(bot))