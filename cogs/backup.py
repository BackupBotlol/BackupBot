import discord
import os
import zipfile
import shutil
import json
import aiohttp
import subprocess
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Dict, Any

class BackupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.backup_jobs = {}
        
        # Initialize scheduler
        self.scheduler = AsyncIOScheduler(
            job_defaults={
                'coalesce': True,
                'max_instances': 1,
                'misfire_grace_time': 3600
            }
        )
        
        # GitHub settings for stat updates
        self.GITHUB_REPO_PATH = os.getenv("GITHUB_REPO_PATH")
        self.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

    async def initialize_relationships(self):
        # Get references to other cogs
        self.db_cog = self.bot.get_cog("DatabaseCog")
        self.utils_cog = self.bot.get_cog("UtilsCog")
        
        # Start scheduler
        self.scheduler.start()
        
        # Load configurations
        await self.initialize_from_db()
    
    async def initialize_from_db(self):
        # Load server configurations from database
        config = self.db_cog.load_server_config()
        now = datetime.now()
        
        for gid_str, cfg in config.items():
            gid = int(gid_str)
            if gid in self.backup_jobs and self.backup_jobs[gid].get('job'):
                continue
                
            self.backup_jobs[gid] = {
                'log_channel_id': cfg['log_channel_id'],
                'job': None,
                'timezone': cfg['timezone'],
                'frequency': cfg['frequency'],
                'preferences': cfg.get('preferences', {})
            }
            
            nbr = cfg.get('next_backup')
            if nbr:
                try:
                    nb = datetime.fromisoformat(nbr)
                    next_run = nb if nb > now else now + timedelta(minutes=5)
                except:
                    next_run = now + timedelta(minutes=30)
            else:
                next_run = now + timedelta(minutes=5)
                
            job = self.scheduler.add_job(
                self.backup_wrapper,
                "date",
                run_date=next_run,
                args=[gid],
                id=f"backup_{gid}",
                replace_existing=True
            )
            self.backup_jobs[gid]['job'] = job
            
        self.update_servers_count()
    
    async def backup_wrapper(self, guild_id: int):
        success = await self.save_server_data(guild_id)
        if success:
            self.db_cog.update_stat('backups_created', inc=1)
            freq = self.backup_jobs[guild_id].get('frequency', 'daily')
            tz = self.backup_jobs[guild_id].get('timezone', 'UTC')
            next_run = self.utils_cog.calculate_next_run(tz, freq)
        else:
            next_run = datetime.now() + timedelta(hours=1)
            
        job = self.scheduler.add_job(
            self.backup_wrapper, "date",
            run_date=next_run,
            args=[guild_id],
            id=f"backup_{guild_id}",
            replace_existing=True
        )
        self.backup_jobs[guild_id]['job'] = job
        self.db_cog.save_server_config(self.backup_jobs)
        if success:
            self.export_stats_to_json()
    
    async def save_server_data(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
            
        log_channel = self.bot.get_channel(self.backup_jobs[guild_id]['log_channel_id'])
        if not log_channel:
            return False
            
        prefs = self.backup_jobs[guild_id].get('preferences', {})
        name = guild.name
        folder = self.utils_cog.sanitize_filename(f"backup_{name}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
        os.makedirs(folder, exist_ok=True)
        
        # Write server info
        with open(os.path.join(folder, "server_info.txt"), 'w', encoding='utf-8') as f:
            f.write(f"Server Name: {name}\nMember Count: {guild.member_count}\nCreated At: {guild.created_at}\nBoosts: {guild.premium_subscription_count}\nBoost Level: {guild.premium_tier}\n")
        
        # Server assets
        if prefs.get('save_server_assets', True):
            assets = os.path.join(folder, "server_assets")
            os.makedirs(assets, exist_ok=True)
            async with aiohttp.ClientSession() as session:
                for attr, fname in [('icon', 'server_icon.png'), ('banner', 'server_banner.png'), ('splash', 'server_splash.png'), ('discovery_splash', 'server_discovery_splash.png')]:
                    url = getattr(guild, attr, None)
                    if url:
                        u = str(url.url)
                        async with session.get(u) as resp:
                            if resp.status == 200:
                                with open(os.path.join(assets, fname), 'wb') as f2:
                                    f2.write(await resp.read())
        
        # Channels
        if prefs.get('save_channels', True):
            with open(os.path.join(folder, "channels.txt"), 'w', encoding='utf-8') as f:
                for cat in guild.categories:
                    f.write(f"[Category] {cat.name}\n")
                    for tc in cat.text_channels:
                        f.write(f"  - {tc.name} (Text)\n")
                    for vc in cat.voice_channels:
                        f.write(f"  - {vc.name} (Voice)\n")
                for tc in guild.text_channels:
                    if tc.category is None:
                        f.write(f"{tc.name} (Text)\n")
                for vc in guild.voice_channels:
                    if vc.category is None:
                        f.write(f"{vc.name} (Voice)\n")
        
        # Roles
        if prefs.get('save_roles', True):
            with open(os.path.join(folder, "roles.txt"), 'w', encoding='utf-8') as f:
                for role in guild.roles:
                    perms = [p for p, v in role.permissions if v]
                    f.write(f"{role.name}: {', '.join(perms)}\n")
        
        # Role icons
        if prefs.get('save_role_icons', True):
            icons = [r for r in guild.roles if r.icon]
            if icons:
                ric = os.path.join(folder, "role_icons")
                os.makedirs(ric, exist_ok=True)
                async with aiohttp.ClientSession() as session:
                    for r in icons:
                        u = str(r.icon.url)
                        async with session.get(u) as resp:
                            if resp.status == 200:
                                ext = os.path.splitext(urlparse(u).path)[1] or ".png"
                                with open(os.path.join(ric, self.utils_cog.sanitize_filename(r.name) + ext), 'wb') as f2:
                                    f2.write(await resp.read())
        
        # Emojis
        if prefs.get('save_emojis', True):
            emo = os.path.join(folder, "emojis")
            os.makedirs(emo, exist_ok=True)
            async with aiohttp.ClientSession() as session:
                for e in guild.emojis:
                    async with session.get(str(e.url)) as resp:
                        if resp.status == 200:
                            ext = "gif" if e.animated else "png"
                            with open(os.path.join(emo, self.utils_cog.sanitize_filename(e.name) + "." + ext), 'wb') as f2:
                                f2.write(await resp.read())
        
        # Stickers
        if prefs.get('save_stickers', True):
            stc = os.path.join(folder, "stickers")
            os.makedirs(stc, exist_ok=True)
            async with aiohttp.ClientSession() as session:
                for s in guild.stickers:
                    u = str(s.url)
                    async with session.get(u) as resp:
                        if resp.status == 200:
                            ext = os.path.splitext(urlparse(u).path)[1] or ".png"
                            with open(os.path.join(stc, self.utils_cog.sanitize_filename(s.name) + ext), 'wb') as f2:
                                f2.write(await resp.read())
        
        # Create ZIP
        zip_name = f"{folder}.zip"
        with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder):
                for file in files:
                    path = os.path.join(root, file)
                    zf.write(path, os.path.relpath(path, folder))
        
        # Upload and send
        size = os.path.getsize(zip_name)
        url = await self.utils_cog.upload_to_cdn(zip_name, guild_id)
        
        try:
            if guild.premium_tier >= 2 and size < ((50 if guild.premium_tier < 3 else 100) * 1024 * 1024):
                with open(zip_name, 'rb') as f:
                    await log_channel.send(f"Server backup for {name} (Boost Level {guild.premium_tier}):\nCDN Link: {url}", file=discord.File(f))
            elif size < self.utils_cog.MAX_DISCORD_FILE_SIZE:
                with open(zip_name, 'rb') as f:
                    await log_channel.send(f"Server backup for {name}:\nCDN Link: {url}", file=discord.File(f))
            else:
                await log_channel.send(f"Server backup for {name}:\n{url}")
        except discord.HTTPException:
            await log_channel.send(f"Backup available at: {url}")
            await self.utils_cog.send_chunked_backup(log_channel, name, zip_name)
        
        # Cleanup
        shutil.rmtree(folder)
        os.remove(zip_name)
        
        # Update stats
        self.db_cog.update_stat('data_saved_bytes', inc=size)
        self.db_cog.record_backup_completion(guild_id)
        return True
    
    def update_servers_count(self):
        active = sum(1 for d in self.backup_jobs.values() if d.get('job'))
        self.db_cog.update_stat('servers_protected', val=active)
        self.export_stats_to_json()
    
    def export_stats_to_json(self):
        stats = self.db_cog.get_stats()
        with open('stats.json', 'w') as f:
            json.dump({
                'servers_protected': int(stats.get('servers_protected', 0)),
                'backups_created': int(stats.get('backups_created', 0)),
                'data_saved_bytes': int(stats.get('data_saved_bytes', 0))
            }, f)
        self.push_stats_to_github('stats.json')
    
    def push_stats_to_github(self, path):
        if not self.GITHUB_REPO_PATH or not self.GITHUB_TOKEN:
            return
        try:
            repo_dir = os.path.expanduser(self.GITHUB_REPO_PATH)
            if not os.path.exists(repo_dir):
                subprocess.run([
                    'git', 'clone',
                    f'https://{self.GITHUB_TOKEN}@github.com/{self.GITHUB_REPO_PATH.split("/")[-2]}/{self.GITHUB_REPO_PATH.split("/")[-1]}.git',
                    repo_dir
                ], check=True)
            else:
                subprocess.run(['git', 'checkout', '-f', 'main'], cwd=repo_dir, check=True)
                subprocess.run(['git', 'fetch', '--all'], cwd=repo_dir, check=True)
                subprocess.run(['git', 'reset', '--hard', 'origin/main'], cwd=repo_dir, check=True)
            target = os.path.join(repo_dir, 'stats.json')
            shutil.copy(path, target)
            subprocess.run(['git', 'config', 'user.name', 'BackupBot'], cwd=repo_dir, check=True)
            subprocess.run(['git', 'config', 'user.email', 'backupbot@example.com'], cwd=repo_dir, check=True)
            subprocess.run(['git', 'add', 'stats.json'], cwd=repo_dir, check=True)
            subprocess.run(['git', 'commit', '-m', f'Update stats - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'], cwd=repo_dir, check=True)
            subprocess.run(['git', 'push', 'origin', 'main'], cwd=repo_dir, check=True)
        except:
            pass
    
    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        gid = guild.id
        if gid in self.backup_jobs:
            job = self.backup_jobs[gid].get('job')
            if job:
                self.scheduler.remove_job(job.id)
            del self.backup_jobs[gid]
            self.db_cog.save_server_config(self.backup_jobs)
            self.update_servers_count()

async def setup(bot):
    await bot.add_cog(BackupCog(bot))