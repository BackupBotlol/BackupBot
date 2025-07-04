import discord
import pytz
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
from typing import Optional

class ServerManagementCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def initialize_relationships(self):
        # Get references to other cogs
        self.db_cog = self.bot.get_cog("DatabaseCog")
        self.utils_cog = self.bot.get_cog("UtilsCog")
        self.backup_cog = self.bot.get_cog("BackupCog")
    
    @app_commands.command(name="help", description="Display all available commands and their descriptions")
    @app_commands.describe(language="Select a language for the help message")
    @app_commands.choices(language=[
        app_commands.Choice(name="Arabic", value="arabic"),
        app_commands.Choice(name="English", value="english")
    ])
    async def help_cmd(self, interaction: discord.Interaction, language: Optional[str]=None):
        if language=="arabic":
            embed = discord.Embed(title="أوامر BackupBot:", color=discord.Color.blue())
            cmds = [
                ("/help","عرض رسالة المساعدة مع جميع أوامر البوت المتاحة"),
                ("/addserver","إضافة هذا السيرفر إلى قائمة النسخ الاحتياطي وتعيين قناة السجل والمنطقة الزمنية"),
                ("/activate","تفعيل النسخ الاحتياطي وصنع نسخة احتياطية فورية"),
                ("/deactivate","تعطيل النسخ الاحتياطي لهذا السيرفر"),
                ("/status","التحقق من حالة النسخ الاحتياطي والموعد التالي"),
                ("/removeserver","إزالة هذا السيرفر من قائمة النسخ الاحتياطي"),
                ("/changetimezone","تغيير المنطقة الزمنية للنسخ الاحتياطي"),
                ("/changefrequency","تغيير تكرار النسخ الاحتياطي"),
                ("/configurebackupcomponents","اختيار مكونات النسخ الاحتياطي"),
                ("/separatefiles","اختيار إرسال الملفات بشكل منفصل أو كملف واحد"),
                ("/ping","اختبار استجابة البوت")
            ]
        else:
            embed = discord.Embed(title="BackupBot Commands:", color=discord.Color.blue())
            cmds = [
                ("/help","Display this help message"),
                ("/addserver","Add your server to the backup list and set the log channel where backup files will be sent and the timezone and the frequency"),
                ("/activate","Activate backup scheduler and run a backup now"),
                ("/deactivate","Deactivate backup scheduler"),
                ("/status","Check scheduler status and when the next backup is scheduled"),
                ("/removeserver","Remove this server from backups"),
                ("/changetimezone","Change backup timezone"),
                ("/changefrequency","Change backup frequency"),
                ("/configurebackupcomponents","Select backup components"),
                ("/separatefiles","Send backups as separate ZIP files or as a single ZIP file"),
                ("/ping","Test the bot's response time")
            ]
        for name, desc in cmds:
            embed.add_field(name=name, value=desc, inline=False)
        embed.set_footer(text="BackupBot • 2025")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="addserver", description="Add this server to the backup list and set the log channel")
    @app_commands.describe(
        log_channel="The channel where backup files will be sent",
        timezone="The timezone for scheduling backups",
        frequency="How often backups should be created"
    )
    @app_commands.choices(frequency=[
        app_commands.Choice(name="Hourly", value="hourly"),
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
        app_commands.Choice(name="Monthly", value="monthly"),
        app_commands.Choice(name="Yearly", value="yearly")
    ])
    async def addserver(self, interaction: discord.Interaction, 
                        log_channel: discord.TextChannel, 
                        timezone: str = "UTC", 
                        frequency: str = "daily"):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        try:
            pytz.timezone(timezone)
        except:
            await interaction.response.send_message(f"Invalid timezone: {timezone}", ephemeral=True)
            return
            
        gid = interaction.guild.id
        perms = log_channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.attach_files):
            await interaction.response.send_message("Missing permissions in that channel.", ephemeral=True)
            return
            
        if gid in self.backup_cog.backup_jobs and self.backup_cog.backup_jobs[gid].get('job'):
            self.backup_cog.scheduler.remove_job(self.backup_cog.backup_jobs[gid]['job'].id)
            msg = "Updated server backup configuration."
        else:
            msg = "Server added to backup list."
            
        await interaction.response.send_message(f"{msg} Running initial backup...", ephemeral=False)
        
        self.backup_cog.backup_jobs[gid] = {
            'log_channel_id': log_channel.id,
            'job': None,
            'timezone': timezone,
            'frequency': frequency,
            'preferences': {
                'save_server_assets': True,
                'save_channels': True,
                'save_roles': True,
                'save_role_icons': True,
                'save_emojis': True,
                'save_stickers': True,
                'separate_component_files': False
            }
        }
        
        success = await self.backup_cog.save_server_data(gid)
        if success:
            next_run = self.utils_cog.calculate_next_run(timezone, frequency)
            job = self.backup_cog.scheduler.add_job(
                self.backup_cog.backup_wrapper, "date",
                run_date=next_run,
                args=[gid],
                id=f"backup_{gid}",
                replace_existing=True
            )
            self.backup_cog.backup_jobs[gid]['job'] = job
            self.db_cog.save_server_config(self.backup_cog.backup_jobs)
            self.backup_cog.update_servers_count()
            await interaction.followup.send(f"Backup completed and scheduler activated. Next backup at {next_run} ({timezone}).")
        else:
            await interaction.followup.send("Backup failed. Please check channel permissions.")

    @app_commands.command(name="changetimezone", description="Change the timezone for backup scheduling")
    async def changetimezone(self, interaction: discord.Interaction, timezone: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        try:
            pytz.timezone(timezone)
        except:
            await interaction.response.send_message(f"Invalid timezone: {timezone}", ephemeral=True)
            return
            
        old = self.backup_cog.backup_jobs[gid]['timezone']
        self.backup_cog.backup_jobs[gid]['timezone'] = timezone
        job = self.backup_cog.backup_jobs[gid].get('job')
        
        if job:
            self.backup_cog.scheduler.remove_job(job.id)
            next_run = self.utils_cog.calculate_next_run(timezone, self.backup_cog.backup_jobs[gid]['frequency'])
            new_job = self.backup_cog.scheduler.add_job(
                self.backup_cog.backup_wrapper, "date",
                run_date=next_run,
                args=[gid],
                id=f"backup_{gid}",
                replace_existing=True
            )
            self.backup_cog.backup_jobs[gid]['job'] = new_job
            self.db_cog.save_server_config(self.backup_cog.backup_jobs)
            await interaction.response.send_message(f"Timezone changed from {old} to {timezone}. Next backup at {next_run}.")
        else:
            self.db_cog.save_server_config(self.backup_cog.backup_jobs)
            await interaction.response.send_message(f"Timezone changed from {old} to {timezone}. Scheduler inactive.")

    @app_commands.command(name="changefrequency", description="Change how often server backups are created")
    @app_commands.choices(frequency=[
        app_commands.Choice(name="Hourly", value="hourly"),
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
        app_commands.Choice(name="Monthly", value="monthly"),
        app_commands.Choice(name="Yearly", value="yearly")
    ])
    async def changefrequency(self, interaction: discord.Interaction, frequency: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        old = self.backup_cog.backup_jobs[gid]['frequency']
        self.backup_cog.backup_jobs[gid]['frequency'] = frequency
        job = self.backup_cog.backup_jobs[gid].get('job')
        
        if job:
            self.backup_cog.scheduler.remove_job(job.id)
            next_run = self.utils_cog.calculate_next_run(self.backup_cog.backup_jobs[gid]['timezone'], frequency)
            new_job = self.backup_cog.scheduler.add_job(
                self.backup_cog.backup_wrapper, "date",
                run_date=next_run,
                args=[gid],
                id=f"backup_{gid}",
                replace_existing=True
            )
            self.backup_cog.backup_jobs[gid]['job'] = new_job
            self.db_cog.save_server_config(self.backup_cog.backup_jobs)
            await interaction.response.send_message(f"Frequency changed from {old} to {frequency}. Next backup at {next_run}.")
        else:
            self.db_cog.save_server_config(self.backup_cog.backup_jobs)
            await interaction.response.send_message(f"Frequency changed from {old} to {frequency}. Scheduler inactive.")

    @app_commands.command(name="configurebackupcomponents", description="Choose what gets saved in your server backups")
    async def configurebackup(self, interaction: discord.Interaction,
        server_assets: Optional[bool] = None,
        channels: Optional[bool] = None,
        roles: Optional[bool] = None,
        role_icons: Optional[bool] = None,
        emojis: Optional[bool] = None,
        stickers: Optional[bool] = None):
        
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        prefs = self.backup_cog.backup_jobs[gid].setdefault('preferences', {
            'save_server_assets': True,
            'save_channels': True,
            'save_roles': True,
            'save_role_icons': True,
            'save_emojis': True,
            'save_stickers': True,
            'separate_component_files': False
        })
        
        changes = []
        if server_assets is not None:
            prefs['save_server_assets'] = server_assets
            changes.append(f"Server assets: {'enabled' if server_assets else 'disabled'}")
        if channels is not None:
            prefs['save_channels'] = channels
            changes.append(f"Channels: {'enabled' if channels else 'disabled'}")
        if roles is not None:
            prefs['save_roles'] = roles
            changes.append(f"Roles: {'enabled' if roles else 'disabled'}")
        if role_icons is not None:
            prefs['save_role_icons'] = role_icons
            changes.append(f"Role icons: {'enabled' if role_icons else 'disabled'}")
        if emojis is not None:
            prefs['save_emojis'] = emojis
            changes.append(f"Emojis: {'enabled' if emojis else 'disabled'}")
        if stickers is not None:
            prefs['save_stickers'] = stickers
            changes.append(f"Stickers: {'enabled' if stickers else 'disabled'}")
            
        self.db_cog.save_server_config(self.backup_cog.backup_jobs)
        
        if changes:
            await interaction.response.send_message("Backup preferences updated:\n" + "\n".join(changes))
        else:
            status = "\n".join(f"{k.replace('save_', '').replace('_', ' ').title()}: {'enabled' if v else 'disabled'}" 
                              for k, v in prefs.items() if k != 'separate_component_files')
            await interaction.response.send_message("Current backup configuration:\n" + status)

    @app_commands.command(name="separatefiles", description="Choose if backups should be sent as separate ZIP files or as a single ZIP file")
    async def setseparatefiles(self, interaction: discord.Interaction, separate_files: bool):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        prefs = self.backup_cog.backup_jobs[gid].setdefault('preferences', {})
        prefs['separate_component_files'] = separate_files
        self.db_cog.save_server_config(self.backup_cog.backup_jobs)
        
        msg = "Backup components will be sent as separate zip files." if separate_files else "Backup components will be sent as a single zip file."
        await interaction.response.send_message(msg)

    @app_commands.command(name="activate", description="Activate the backup scheduler and run a backup immediately")
    async def activate(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        if self.backup_cog.backup_jobs[gid].get('job'):
            await interaction.response.send_message("Scheduler is already active.", ephemeral=False)
            return
            
        await interaction.response.send_message("Running backup and activating scheduler...", ephemeral=False)
        
        success = await self.backup_cog.save_server_data(gid)
        if success:
            next_run = datetime.now() + timedelta(days=1)
            job = self.backup_cog.scheduler.add_job(
                self.backup_cog.backup_wrapper, "date",
                run_date=next_run,
                args=[gid],
                id=f"backup_{gid}",
                replace_existing=True
            )
            self.backup_cog.backup_jobs[gid]['job'] = job
            self.db_cog.save_server_config(self.backup_cog.backup_jobs)
            self.backup_cog.update_servers_count()
            await interaction.followup.send(f"Backup completed and scheduler activated. Next backup is at {next_run}.")
        else:
            await interaction.followup.send("Backup failed. Please check the bot's permissions.")

    @app_commands.command(name="deactivate", description="Deactivate the backup scheduler for this server")
    async def deactivate(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        job = self.backup_cog.backup_jobs[gid].get('job')
        if not job:
            await interaction.response.send_message("The scheduler is not active.", ephemeral=False)
            return
            
        self.backup_cog.scheduler.remove_job(job.id)
        self.backup_cog.backup_jobs[gid]['job'] = None
        self.db_cog.save_server_config(self.backup_cog.backup_jobs)
        self.backup_cog.update_servers_count()
        await interaction.response.send_message("Scheduler deactivated.", ephemeral=False)

    @app_commands.command(name="status", description="Check if the backup scheduler is active or deactivated for this server")
    async def status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=True)
            return
            
        job = self.backup_cog.backup_jobs[gid].get('job')
        if not job:
            await interaction.response.send_message("Scheduler is inactive.", ephemeral=False)
        else:
            await interaction.response.send_message(f"Scheduler is active. Next backup at {job.next_run_time}.", ephemeral=False)

    @app_commands.command(name="removeserver", description="Remove this server from the backup list")
    async def removeserver(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server, not in DMs.", ephemeral=True)
            return
            
        gid = interaction.guild.id
        if gid not in self.backup_cog.backup_jobs:
            await interaction.response.send_message("This server is not configured. Please use `/addserver` first.", ephemeral=False)
            return
            
        job = self.backup_cog.backup_jobs[gid].get('job')
        if job:
            self.backup_cog.scheduler.remove_job(job.id)
            
        del self.backup_cog.backup_jobs[gid]
        self.db_cog.save_server_config(self.backup_cog.backup_jobs)
        self.backup_cog.update_servers_count()
        await interaction.response.send_message("Server removed from backup list.", ephemeral=False)

async def setup(bot):
    await bot.add_cog(ServerManagementCog(bot))