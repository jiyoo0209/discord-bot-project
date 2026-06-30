'''
* 명예/우수 설정
* author HDG
* date   2026.06.30
'''
import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
 
load_dotenv()
 
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# 등급번호 → 역할 ID
# 해당 ID 디스코드에서 확인 후 기입 예정 (서버 설정 -> 역할 -> 기타에서 확인 가능)
# .env 파일이나, config 파일 하나 만들어서 기입하면 될듯??
ROLE_IDS = {
    1: 1,  # 길드마스터
    2: 2,  # 서브마스터
    3: 3,  # 명예
    4: 4,  # 우수
    9: 9,  # 일반
}


@bot.event
async def on_ready():
    await bot.tree.sync()
 
def has_role_level(interaction: discord.Interaction, level: int) -> bool:
    """명령어를 친 사람이 해당 등급의 역할을 가지고 있는지 확인"""
    target_id = ROLE_IDS[level]
    user_role_ids = [role.id for role in interaction.user.roles]
    return target_id in user_role_ids
 
@bot.tree.command(name='명예설정', description='명예길드원 인원수를 설정')
@app_commands.describe(숫자='설정할 명예길드원 수 (예: 3)')
async def honor_setting(interaction: discord.Interaction, 숫자: int):
    if not has_role_level(interaction, 1):    # 길드마스터(등급 1)만 사용 가능
        await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
        return
    await interaction.response.send_message( f'명예길드원이 {숫자}명으로 설정되었습니다!' )
 
@bot.tree.command(name='우수설정', description='우수길드원 인원수를 설정')
@app_commands.describe(숫자='설정할 우수길드원 수 (예: 3)')
async def excellent_setting(interaction: discord.Interaction, 숫자: int):
    if not has_role_level(interaction, 1):     # 길드마스터(등급 1)만 사용 가능
        await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
        return
    await interaction.response.send_message(f'우수길드원이 {숫자}명으로 설정되었습니다!')
 
bot.run(os.getenv('DISCORD_TOKEN'))
 