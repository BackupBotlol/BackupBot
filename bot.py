import discord
import os
from dotenv import load_dotenv
from discord.ext import commands

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Set up bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.emojis_and_stickers = True
intents.voice_states = True

class BackupBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="/", intents=intents)
        
    async def setup_hook(self):
        # Load cogs in order of dependency
        await self.load_extension('cogs.database')
        await self.load_extension('cogs.utils')
        await self.load_extension('cogs.backup')
        await self.load_extension('cogs.server_management')
        
        # Initialize relationships between cogs after loading all cogs
        for cog_name, cog in self.cogs.items():
            if hasattr(cog, 'initialize_relationships'):
                await cog.initialize_relationships()
        
        print("All cogs loaded and initialized")
        
    async def on_ready(self):
        print(f"Logged in as {self.user}")
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="backupbot.net | /help"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)
        
        await self.tree.sync()
        print("Command tree synced")

# Run the bot
if __name__ == "__main__":
    bot = BackupBot()
    bot.run(TOKEN, reconnect=True)