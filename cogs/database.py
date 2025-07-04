import discord
import os
import sqlite3
import json
import pytz
from typing import Dict, Any
from discord.ext import commands
from datetime import datetime

class DatabaseCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.CONFIG_FILE = "server_config.json"
        self.DB_FILE = "backup_config.db"
        self.setup_database()
        
    async def initialize_relationships(self):
        # This cog doesn't need references to others
        pass
        
    def setup_database(self):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS server_configs (
            guild_id INTEGER PRIMARY KEY,
            log_channel_id INTEGER NOT NULL,
            next_backup TEXT,
            active BOOLEAN NOT NULL DEFAULT 0,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            frequency TEXT NOT NULL DEFAULT 'daily'
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS backup_preferences (
            guild_id INTEGER PRIMARY KEY,
            save_server_assets BOOLEAN NOT NULL DEFAULT 1,
            save_channels BOOLEAN NOT NULL DEFAULT 1,
            save_roles BOOLEAN NOT NULL DEFAULT 1,
            save_role_icons BOOLEAN NOT NULL DEFAULT 1,
            save_emojis BOOLEAN NOT NULL DEFAULT 1,
            save_stickers BOOLEAN NOT NULL DEFAULT 1,
            separate_component_files BOOLEAN NOT NULL DEFAULT 0,
            FOREIGN KEY (guild_id) REFERENCES server_configs(guild_id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bot_stats (
            stat_name TEXT PRIMARY KEY,
            stat_value REAL NOT NULL
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS backup_history (
            guild_id INTEGER,
            backup_time TEXT,
            PRIMARY KEY (guild_id, backup_time)
        )''')
        cursor.execute("INSERT OR IGNORE INTO bot_stats VALUES ('servers_protected',0)")
        cursor.execute("INSERT OR IGNORE INTO bot_stats VALUES ('backups_created',0)")
        cursor.execute("INSERT OR IGNORE INTO bot_stats VALUES ('data_saved_bytes',0)")
        conn.commit()
        conn.close()
    
    def migrate_json_to_sqlite(self):
        if os.path.exists(self.CONFIG_FILE):
            with open(self.CONFIG_FILE) as f:
                cfg = json.load(f)
            conn = sqlite3.connect(self.DB_FILE)
            cursor = conn.cursor()
            for gid_str, v in cfg.items():
                gid = int(gid_str)
                cursor.execute("INSERT OR REPLACE INTO server_configs VALUES (?,?,?,?,?,?)",
                    (gid, v['log_channel_id'], v.get('next_backup'), v.get('active', False), v.get('timezone','UTC'), v.get('frequency','daily'))
                )
                cursor.execute("INSERT OR REPLACE INTO backup_preferences VALUES (?,?,?,?,?,?,?,?)",
                    (gid,1,1,1,1,1,1,0)
                )
            conn.commit()
            conn.close()
            os.rename(self.CONFIG_FILE, self.CONFIG_FILE + ".bak")
    
    def migrate_gb_to_bytes(self):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT stat_value FROM bot_stats WHERE stat_name='data_saved_gb'")
        row = cursor.fetchone()
        if row:
            cursor.execute("INSERT OR REPLACE INTO bot_stats VALUES ('data_saved_bytes',?)", (int(row[0]*1024**3),))
        conn.commit()
        conn.close()
    
    def update_stat(self, name, inc=None, val=None):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        if inc is not None:
            cursor.execute("UPDATE bot_stats SET stat_value=stat_value+? WHERE stat_name=?", (inc, name))
        elif val is not None:
            cursor.execute("UPDATE bot_stats SET stat_value=? WHERE stat_name=?", (val, name))
        conn.commit()
        conn.close()
    
    def get_stats(self):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT stat_name,stat_value FROM bot_stats")
        stats = {r[0]:r[1] for r in cursor.fetchall()}
        conn.close()
        return stats
    
    def save_server_config(self, backup_jobs):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        for guild_id, job_data in backup_jobs.items():
            next_run = job_data['job'].next_run_time.isoformat() if job_data.get('job') and job_data['job'].next_run_time else None
            cursor.execute(
                "INSERT OR REPLACE INTO server_configs VALUES (?,?,?,?,?,?)",
                (guild_id, job_data['log_channel_id'], next_run, bool(job_data.get('job')), job_data.get('timezone','UTC'), job_data.get('frequency','daily'))
            )
            prefs = job_data.get('preferences', {})
            cursor.execute(
                "INSERT OR REPLACE INTO backup_preferences VALUES (?,?,?,?,?,?,?,?)",
                (
                    guild_id,
                    prefs.get('save_server_assets', True),
                    prefs.get('save_channels', True),
                    prefs.get('save_roles', True),
                    prefs.get('save_role_icons', True),
                    prefs.get('save_emojis', True),
                    prefs.get('save_stickers', True),
                    prefs.get('separate_component_files', False)
                )
            )
        conn.commit()
        conn.close()
    
    def load_server_config(self):
        self.setup_database()
        conn = sqlite3.connect(self.DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM server_configs")
        config = {}
        for row in cursor.fetchall():
            gid = row['guild_id']
            config[str(gid)] = {
                'log_channel_id': row['log_channel_id'],
                'next_backup': row['next_backup'],
                'active': bool(row['active']),
                'timezone': row['timezone'],
                'frequency': row['frequency']
            }
            pref = conn.execute("SELECT * FROM backup_preferences WHERE guild_id=?", (gid,)).fetchone()
            config[str(gid)]['preferences'] = {
                'save_server_assets': bool(pref['save_server_assets']),
                'save_channels': bool(pref['save_channels']),
                'save_roles': bool(pref['save_roles']),
                'save_role_icons': bool(pref['save_role_icons']),
                'save_emojis': bool(pref['save_emojis']),
                'save_stickers': bool(pref['save_stickers']),
                'separate_component_files': bool(pref['separate_component_files'])
            } if pref else {
                'save_server_assets': True,
                'save_channels': True,
                'save_roles': True,
                'save_role_icons': True,
                'save_emojis': True,
                'save_stickers': True,
                'separate_component_files': False
            }
        conn.close()
        return config
    
    def record_backup_completion(self, guild_id):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO backup_history VALUES (?, ?)", (guild_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def get_last_backup_time(self, guild_id):
        conn = sqlite3.connect(self.DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(backup_time) FROM backup_history WHERE guild_id = ?", (guild_id,))
        result = cursor.fetchone()
        conn.close()
        if result and result[0]:
            return datetime.fromisoformat(result[0])
        return None

async def setup(bot):
    await bot.add_cog(DatabaseCog(bot))