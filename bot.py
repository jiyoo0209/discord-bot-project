import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# 슬래시 커맨드만 쓰므로 message_content(특권 인텐트) 불필요 — 포털 설정도 필요 없음
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='/', intents=intents)

_synced = False  # on_ready 는 재연결마다 불리므로 동기화 1회만

@bot.event
async def on_ready():
    global _synced
    print(f'봇 온라인: {bot.user}')
    if _synced:
        return
    guild_id = os.getenv('GUILD_ID')
    if guild_id:
        # 단일 길드 테스트: 해당 길드에 즉시 반영 (글로벌은 최대 1시간 걸림)
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        # 예전에 글로벌로 등록됐던 명령 제거 → 중복(글로벌+길드) 방지.
        #   글로벌 '삭제'는 디스코드 특성상 전파에 최대 1시간 걸릴 수 있음.
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        print(f'명령어 동기화 완료 (길드 {guild_id} · 즉시 / 글로벌 중복 정리)')
    else:
        await bot.tree.sync()
        print('명령어 동기화 완료 (글로벌 · 전파에 최대 1시간)')
    _synced = True

@bot.tree.command(name='test', description='테스트 명령어')
async def test(interaction: discord.Interaction):
    await interaction.response.send_message('정상 작동!')

@bot.tree.command(name='관리자', description='길마,서마만 사용 가능')
@discord.app_commands.guild_only()
async def admin_command(interaction: discord.Interaction):
    # 허용할 역할 목록
    allowed_roles = ['💜길마', '🩵서마']

    # 사용자의 역할 이름 목록
    user_roles = [role.name for role in interaction.user.roles]

    # 허용된 역할이 있는지 확인
    has_permission = any(role in user_roles for role in allowed_roles)

    if not has_permission:
        await interaction.response.send_message('길마, 서마만 사용 가능합니다!', ephemeral=True)
        return

    await interaction.response.send_message('관리자입니다!')

async def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print('❌ DISCORD_TOKEN 이 없습니다. .env 파일을 확인하세요 (.env.example 참고)')
        return
    async with bot:
        await bot.load_extension('roleSetting.roleSetting')
        await bot.load_extension('user.user')
        await bot.load_extension('dashboard.dashboard')
        await bot.load_extension('quest.quest')
        await bot.load_extension('batch.batch')
        await bot.load_extension('panel.panel')
        # 나중에 다른 파일도 이렇게 추가

        # bot 실행
        await bot.start(token)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('봇 종료됨')  # Ctrl+C — 정상 종료 (트레이스백 숨김)