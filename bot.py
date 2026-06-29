import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    print(f' 봇 온라인: {bot.user}')
    await bot.tree.sync()
    print('명령어 동기화 완료')

@bot.tree.command(name='test', description='테스트 명령어')
async def test(interaction: discord.Interaction):
    await interaction.response.send_message('정상 작동!')

bot.run(os.getenv('DISCORD_TOKEN'))