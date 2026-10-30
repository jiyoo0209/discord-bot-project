import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# message_content(특권 인텐트): 음성방 감시 채널의 글을 읽기 위해 필요 — 개발자 포털에서도 켜져 있어야 함
intents = discord.Intents.default()
intents.message_content = True
intents.members = True   # 서버 입장 감지(입장 카드) — 개발자 포털 Server Members Intent 도 켜야 함
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
        await bot.load_extension('voice.voice')
        await bot.load_extension('welcome.welcome')
        await bot.load_extension('vote.vote')
        await bot.load_extension('memberlist.memberlist')
        await bot.load_extension('fishing.fishing')
        # 나중에 다른 파일도 이렇게 추가

        # bot 실행
        await bot.start(token)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('봇 종료됨')  # Ctrl+C — 정상 종료 (트레이스백 숨김)