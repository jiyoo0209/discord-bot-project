'''
* 음성방 자동 생성 — 감시 채널에 글이 올라오면 그 글 그대로 음성채널을 만들고 카드로 안내
*   - 카드의 [종료] 버튼(누구나) → 음성채널 삭제(안에 있던 사람은 자동으로 연결 끊김) + 카드를 종료 표시로 교체
*   - 사람이 모두 나가면(또는 만든 뒤 아무도 안 들어오면) 3분 뒤 자동 종료
*   - 같은 이름의 방이 아직 진행 중이면 새로 만들지 않고 그 방으로 안내
*   - 시트 저장 없음: 시작 시각 = 카드 메시지 작성 시각, 재시작 시 감시 채널의 카드를 훑어 진행 중인 방 복구
* date   2026.10.28
'''
import re
import asyncio
from datetime import datetime

import discord
from discord.ext import commands

from util.dates import KST

WATCH_CHANNEL_ID = 1564959685780447383   # 이 채널에 올라온 글 → 음성방 생성
CATEGORY_ID = 1414412254710861977        # 음성방을 만들 카테고리 (맨 아래에 추가)
EMPTY_GRACE_SEC = 180                    # 비어 있으면 3분 뒤 자동 종료
RECOVER_SCAN = 200                       # 재시작 시 감시 채널에서 훑을 최근 메시지 수

_ACCENT = discord.Colour(0x3FB8F3)       # 길드 대표색 (다른 카드와 통일)
_END_ID = re.compile(r'^vc_end:(\d+)$')


def _fmt(dt):
    return dt.astimezone(KST).strftime('%Y/%m/%d %H:%M')


# [종료] 버튼 — custom_id 에 음성채널 ID 를 담아 재시작 후에도 동작 (bot.add_dynamic_items 로 등록)
class EndRoomButton(discord.ui.DynamicItem[discord.ui.Button], template=r'vc_end:(?P<ch>[0-9]+)'):
    def __init__(self, ch_id: int):
        super().__init__(discord.ui.Button(
            label='종료', style=discord.ButtonStyle.danger, custom_id=f'vc_end:{ch_id}'))
        self.ch_id = ch_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['ch']))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog('VoiceRoom')
        await interaction.response.defer()   # 채널 삭제가 3초를 넘길 수 있어 먼저 ack
        if cog is not None:
            await cog.end_room(interaction.guild, self.ch_id, interaction.message)


def _room_card(name, ch_id):
    v = discord.ui.LayoutView(timeout=None)
    row = discord.ui.ActionRow()
    row.add_item(EndRoomButton(ch_id))
    v.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f'**{name}** 음성채널을 만들었습니다.\n<#{ch_id}>'),
        discord.ui.Separator(),
        row,
        accent_colour=_ACCENT,
    ))
    return v


def _ended_card(name, start, end):
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f'**{name}** 채널 사용이 종료되었습니다.\n{_fmt(start)} ~ {_fmt(end)}'),
        accent_colour=_ACCENT,
    ))
    return v


def _info_card(text):
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_colour=_ACCENT))
    return v


def _walk(components):
    '''V2 메시지 컴포넌트 트리를 평탄하게 순회'''
    for c in components or []:
        yield c
        yield from _walk(getattr(c, 'children', None) or getattr(c, 'components', None))


def _card_info(message):
    '''봇 카드 메시지 → (음성채널 ID, 방 이름). 진행 중 카드가 아니면 (None, None).'''
    ch_id = name = None
    for c in _walk(message.components):
        cid = getattr(c, 'custom_id', None)
        if cid and _END_ID.match(cid):
            ch_id = int(_END_ID.match(cid).group(1))
        content = getattr(c, 'content', None)
        if content and name is None:
            m = re.search(r'\*\*(.+?)\*\*', content)
            if m:
                name = m.group(1)
    return ch_id, name


class VoiceRoom(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rooms = {}        # 음성채널 ID -> {'name', 'msg_id'} (진행 중인 방)
        self.timers = {}       # 음성채널 ID -> 3분 자동 종료 Task
        self._ending = set()   # 종료 처리 중 (버튼·타이머 동시 실행 방지)
        self._lock = asyncio.Lock()
        self._recovered = False

    async def cog_load(self):
        self.bot.add_dynamic_items(EndRoomButton)

    def cog_unload(self):
        for t in self.timers.values():
            t.cancel()

    # ── 생성 ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or message.channel.id != WATCH_CHANNEL_ID:
            return
        name = ' '.join((message.content or '').split())[:100]   # 줄바꿈/연속공백 정리, 채널명 100자 제한
        if not name:
            return   # 첨부만 있는 글 등

        async with self._lock:   # 같은 이름이 동시에 두 번 올라와도 방은 하나만
            for ch_id, room in self.rooms.items():
                if room['name'] == name and message.guild.get_channel(ch_id) is not None:
                    await message.reply(
                        view=_info_card(f'이미 **{name}** 음성채널이 있어요.\n<#{ch_id}>'),
                        mention_author=False)
                    return

            category = message.guild.get_channel(CATEGORY_ID)
            try:
                vc = await message.guild.create_voice_channel(name, category=category)
                try:
                    await vc.move(end=True, category=category, sync_permissions=False)   # 카테고리 맨 아래
                except Exception as e:
                    print(f'[voice] 위치 이동 건너뜀: {e}')
            except discord.Forbidden:
                await message.reply('음성채널을 만들 권한이 없습니다. 봇에게 **채널 관리** 권한을 주세요.',
                                    mention_author=False)
                return
            except Exception as e:
                print(f'[voice] 생성 실패: {e}')
                return

            card = await message.reply(view=_room_card(name, vc.id), mention_author=False)
            self.rooms[vc.id] = {'name': name, 'msg_id': card.id}
        self._schedule(vc)   # 만든 뒤 아무도 안 들어와도 3분 뒤 정리

    # ── 종료 (버튼 / 자동) ─────────────────────────────────
    async def end_room(self, guild, ch_id, message=None):
        if ch_id in self._ending:
            return
        self._ending.add(ch_id)
        try:
            room = self.rooms.pop(ch_id, None)
            t = self.timers.pop(ch_id, None)
            if t and t is not asyncio.current_task():
                t.cancel()

            ch = guild.get_channel(ch_id) if guild else None
            if room is None and ch is None and message is not None and _card_info(message)[0] is None:
                return   # 이미 종료된 카드 (동시 클릭 등) → 종료 시각을 덮어쓰지 않음
            name = (room or {}).get('name') or (ch.name if ch else None)
            if message is None and room:
                try:
                    message = await self.bot.get_channel(WATCH_CHANNEL_ID).fetch_message(room['msg_id'])
                except Exception:
                    message = None
            if not name and message is not None:
                name = _card_info(message)[1]

            if ch is not None:
                try:
                    await ch.delete(reason='음성방 종료')   # 안에 있던 사람은 디스코드가 자동으로 연결 해제
                except discord.NotFound:
                    pass
                except Exception as e:
                    print(f'[voice] 채널 삭제 실패({ch_id}): {e}')

            if message is not None:
                try:
                    await message.edit(view=_ended_card(name or '음성', message.created_at,
                                                        datetime.now(KST)))
                except Exception as e:
                    print(f'[voice] 카드 갱신 실패: {e}')
        finally:
            self._ending.discard(ch_id)

    # ── 3분 자동 종료 ─────────────────────────────────────
    def _schedule(self, ch):
        old = self.timers.pop(ch.id, None)
        if old:
            old.cancel()
        self.timers[ch.id] = asyncio.create_task(self._expire(ch.guild, ch.id))

    async def _expire(self, guild, ch_id):
        try:
            await asyncio.sleep(EMPTY_GRACE_SEC)
        except asyncio.CancelledError:
            return
        ch = guild.get_channel(ch_id)
        if ch is not None and ch.members:
            self.timers.pop(ch_id, None)   # 그 사이 누가 들어와 있음 → 취소
            return
        await self.end_room(guild, ch_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if after.channel is not None and after.channel.id in self.rooms:
            t = self.timers.pop(after.channel.id, None)   # 누가 들어옴 → 타이머 취소
            if t:
                t.cancel()
        if (before.channel is not None and before.channel.id in self.rooms
                and before.channel != after.channel and not before.channel.members):
            self._schedule(before.channel)                # 마지막 사람이 나감 → 3분 타이머

    # ── 재시작 복구: 감시 채널의 진행 중 카드로 방 목록 재구성 ─────────
    @commands.Cog.listener()
    async def on_ready(self):
        if self._recovered:
            return
        self._recovered = True
        try:
            channel = self.bot.get_channel(WATCH_CHANNEL_ID) or await self.bot.fetch_channel(WATCH_CHANNEL_ID)
            async for msg in channel.history(limit=RECOVER_SCAN):
                if msg.author.id != self.bot.user.id:
                    continue
                ch_id, name = _card_info(msg)
                if ch_id is None or ch_id in self.rooms:
                    continue
                vc = channel.guild.get_channel(ch_id)
                if vc is None:
                    await self.end_room(channel.guild, ch_id, msg)   # 채널이 이미 없음 → 카드만 종료 표시
                    continue
                self.rooms[ch_id] = {'name': name or vc.name, 'msg_id': msg.id}
                if not vc.members:
                    self._schedule(vc)
        except Exception as e:
            print(f'[voice] 복구 실패: {e}')


async def setup(bot):
    await bot.add_cog(VoiceRoom(bot))
