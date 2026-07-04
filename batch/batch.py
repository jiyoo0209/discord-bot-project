'''
* 배치 작업 — 시간당 루프
*   1) 설치된 대시보드 메시지 자동 갱신 (명세 #10)
*   2) 일요일 점령 미참여 경고 차감 (명세 #4, 주 1회 가드)
* + /주간경고처리 : 수동 실행 (테스트/보정용)
* author ENI
* date   2026.07.01
'''
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks

from datetime import time as dtime, timezone, timedelta

from sheet.sheet import decrement_warnings_nonparticipants, get_incomplete_members
from dashboard.dashboard import build_embed, WEEKLY_QUEST_LIMIT, write_summary_sheet
from roleSetting.roleSetting import has_role
from config.guild_config import get_setting, set_setting
from util.dates import is_sunday, week_range

# 리마인드는 시스템 타임존과 무관하게 **KST 00:00** 에 고정 발송 (tz-aware loop time)
_KST = timezone(timedelta(hours=9))


@app_commands.guild_only()
class Batch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.hourly.start()
        self.midnight.start()

    def cog_unload(self):
        self.hourly.cancel()
        self.midnight.cancel()

    @tasks.loop(hours=1)
    async def hourly(self):
        await self._refresh_dashboards()
        try:
            await asyncio.to_thread(write_summary_sheet)  # 요약 탭도 매시간 최신화
        except Exception as e:
            print(f'[batch] 요약 탭 갱신 실패: {e}')
        await self._weekly_warning()

    @hourly.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # 매일 KST 자정 정각 실행 — 일요일이면 지난 주 길퀘 미완료자 리마인드
    @tasks.loop(time=dtime(hour=0, minute=0, tzinfo=_KST))
    async def midnight(self):
        if is_sunday():
            await self._sunday_reminder()

    @midnight.before_loop
    async def _before_midnight(self):
        await self.bot.wait_until_ready()

    async def _edit_stored(self, guild, ch_key, msg_key, embed_factory):
        '''저장된 (채널,메시지) 를 찾아 embed 만 교체 (버튼 등 컴포넌트는 유지)'''
        ch_id = get_setting(guild.id, ch_key)
        msg_id = get_setting(guild.id, msg_key)
        if not ch_id or not msg_id:
            return
        try:
            channel = guild.get_channel(int(ch_id)) or await self.bot.fetch_channel(int(ch_id))
            message = await channel.fetch_message(int(msg_id))
            embed = await embed_factory(message)
            await message.edit(embed=embed)
        except discord.NotFound:
            # 채널/메시지가 삭제됨 → 등록 해제해서 매시간 404 반복 안 나게
            set_setting(guild.id, ch_key, '')
            set_setting(guild.id, msg_key, '')
            print(f'[batch] {msg_key} 대상 삭제됨 → 등록 해제 (다시 /패널설치·/셋업 하면 재등록)')
        except Exception as e:
            print(f'[batch] 갱신 실패({msg_key}, guild {guild.id}): {e}')

    # 설치된 정적 대시보드(/대시보드설치) 를 최신 데이터로 edit
    async def _refresh_dashboards(self):
        for guild in self.bot.guilds:
            await self._edit_stored(guild, '_dash_ch', '_dash_msg',
                                    lambda m: asyncio.to_thread(build_embed))

    # 일요일 1회: 점령 미참여자 경고 차감. 이번 주(월요일 int)를 가드키로 중복 실행 방지.
    async def _weekly_warning(self):
        if not is_sunday():
            return
        week_id = week_range()[0]
        if get_setting('_global', '_warn_week') == week_id:
            return
        ok, msg = await asyncio.to_thread(decrement_warnings_nonparticipants)
        # 시트 실패(ok=False) 시엔 처리완료로 마킹하지 않는다 → 다음 tick 에 재시도.
        #   안 그러면 일시 오류 한 번에 그 주 경고차감이 통째로 스킵됨.
        if ok:
            set_setting('_global', '_warn_week', week_id)
        print(f'[batch] {msg}')

    # 일요일 자정 1회: 지난 주(월~토) 길퀘 미완료자에게 셋업 채널로 자동 리마인드 (주 1회 가드)
    async def _sunday_reminder(self):
        week_id = week_range()[0]
        if get_setting('_global', '_remind_week') == week_id:
            return
        members = await asyncio.to_thread(get_incomplete_members, WEEKLY_QUEST_LIMIT)
        if members is None:
            return  # 시트 조회 실패 → 가드 안 세팅, 다음 tick 재시도 (그 주 통째 스킵 방지)
        set_setting('_global', '_remind_week', week_id)  # 정상 조회됨 → 주1회 확정
        if not members:
            return
        tags = [(f"<@{m['discord_id']}>" if m['discord_id'] else m['name']) for m in members]
        pings = discord.AllowedMentions(users=True)
        # 데이터는 시트 1개(=한 길드) → 여러 서버 중복 발송 금지. 설정된 '첫' 채널에만.
        for guild in self.bot.guilds:
            ch_id = get_setting(guild.id, '_setup_ch')
            if not ch_id:
                continue
            try:
                channel = guild.get_channel(int(ch_id)) or await self.bot.fetch_channel(int(ch_id))
                cur = '⏰ **이번주 남은 길퀘가 있어요!**'
                for t in tags:                       # 2000자 초과 방지 분할
                    if len(cur) + 1 + len(t) > 1900:
                        await channel.send(cur, allowed_mentions=pings)
                        cur = t
                    else:
                        cur += ' ' + t
                await channel.send(cur, allowed_mentions=pings)
            except Exception as e:
                print(f'[batch] 리마인드 실패(guild {guild.id}): {e}')
            break  # 첫 설정 길드에만 발송

    # 수동 실행 (일요일 안 기다리고 테스트/보정)
    @app_commands.command(name='주간경고처리', description='(길마) 점령 미참여 경고 차감 수동 실행')
    async def run_warning(self, interaction: discord.Interaction):
        if not has_role(interaction, '길마'):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, msg = await asyncio.to_thread(decrement_warnings_nonparticipants)
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Batch(bot))
