'''
* 서버 로그 (한글) — 로그 채널에 임베드로 기록
*   📥 음성 입장 / 📤 음성 퇴장(머문 시간) / 🔀 음성 이동
*   🗑️ 지정 채널 메시지 삭제 (내용 · 작성/삭제 시각) — 봇 시작 전 메시지는 내용을 알 수 없음
*   🖼️ 프로필 사진 변경 (기본·서버 프로필) / ✏️ 이름 변경 (서버 닉네임·사용자명·표시 이름)
*   필요: Server Members Intent, Message Content Intent (둘 다 이미 사용 중)
* date   2026.10.30
'''
import io
from collections import OrderedDict
from datetime import datetime, timezone

import discord
from discord.ext import commands

from util.dates import KST

LOG_CHANNEL_ID = 1417307153739284501
DELETE_WATCH_CHANNEL_ID = 1414434694308696175   # 이 채널의 메시지 삭제만 기록

C_JOIN, C_LEAVE, C_MOVE = discord.Colour(0x3BA55C), discord.Colour(0xED4245), discord.Colour(0x5865F2)
C_DELETE, C_PROFILE = discord.Colour(0x888780), discord.Colour(0xFAA61A)


def _now():
    return datetime.now(timezone.utc)


def _hm(dt):
    return dt.astimezone(KST).strftime('%Y/%m/%d %H:%M')


def _dur(sec):
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    if h:
        return f'{h}시간 {m}분' if m else f'{h}시간'
    return f'{m}분' if m else '1분 미만'


def _who(user):
    '''멘션 대신 서버 닉네임을 굵은 글자로 — 임베드 안 멘션은 보는 사람 앱에 따라 <@ID> 로 깨져 보임'''
    return f'**{discord.utils.escape_markdown(user.display_name)}**'


def _vc(channel):
    '''음성채널 이름을 글자로 — 멘션(<#ID>)은 임시채널이 삭제되면 '알 수 없음' 으로 보임'''
    return f'🔊 `{channel.name.replace("`", "")}`'


# 지워진 첨부 보존: 감시 채널에 올라온 첨부를 미리 내려받아 두고(메모리, 오래된 것부터 정리),
#   삭제되면 로그 채널에 파일로 다시 올림 → 로그 메시지에서 계속 열어볼 수 있음
FILE_MAX = 8 * 1024 * 1024        # 파일 1개 최대 (디스코드 업로드 한도 고려)
STORE_MAX = 60 * 1024 * 1024      # 보관 총량 (넘으면 오래된 메시지부터 버림)


def _embed(user, colour, text, footer=None, thumb=None):
    '''MEE6 로그와 같은 구성: 작성자(사용자명+프로필) · 내용 · (하단 문구) · 시각'''
    e = discord.Embed(description=text, colour=colour, timestamp=_now())
    if user is not None:
        e.set_author(name=user.name, icon_url=user.display_avatar.url)
    if footer:
        e.set_footer(text=footer)
    if thumb:
        e.set_thumbnail(url=thumb)
    return e


class ServerLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_since = {}   # 멤버 ID -> 지금 음성채널에 들어온 시각 (머문 시간용, 재시작 시 초기화)
        self.files = OrderedDict()   # 메시지 ID -> [(파일명, bytes, spoiler)]
        self.files_size = 0

    async def _send(self, embed, files=None):
        try:
            ch = self.bot.get_channel(LOG_CHANNEL_ID) or await self.bot.fetch_channel(LOG_CHANNEL_ID)
            await ch.send(embed=embed, files=files or [])
        except Exception as e:
            print(f'[serverlog] 로그 발송 실패: {e}')

    # ── 음성 입장 / 퇴장 / 이동 ────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or before.channel == after.channel:
            return   # 마이크·화면공유 등 상태 변경은 무시
        if before.channel is None:
            self.voice_since[member.id] = _now()
            await self._send(_embed(member, C_JOIN,
                                    f'📥 {_who(member)} 님이 음성채널에 들어왔어요 {_vc(after.channel)}'))
        elif after.channel is None:
            since = self.voice_since.pop(member.id, None)
            await self._send(_embed(member, C_LEAVE,
                                    f'📤 {_who(member)} 님이 음성채널에서 나갔어요 {_vc(before.channel)}',
                                    footer=f'머문 시간 {_dur((_now() - since).total_seconds())}' if since else None))
        else:
            self.voice_since[member.id] = _now()   # 머문 시간은 채널 기준
            await self._send(_embed(member, C_MOVE,
                                    f'🔀 {_who(member)} 님이 음성채널을 옮겼어요 '
                                    f'{_vc(before.channel)} → {_vc(after.channel)}'))

    # ── 첨부 미리 보관 (지정 채널) ──────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id != DELETE_WATCH_CHANNEL_ID or message.author.bot or not message.attachments:
            return
        saved = []
        for a in message.attachments[:10]:
            if a.size > FILE_MAX:
                continue
            try:
                saved.append((a.filename, await a.read(), a.is_spoiler()))
            except Exception as e:
                print(f'[serverlog] 첨부 보관 실패({a.filename}): {e}')
        if not saved:
            return
        self.files[message.id] = saved
        self.files_size += sum(len(b) for _, b, _ in saved)
        while self.files_size > STORE_MAX and self.files:
            _mid, old = self.files.popitem(last=False)
            self.files_size -= sum(len(b) for _, b, _ in old)

    # ── 메시지 삭제 (지정 채널) ─────────────────────────────
    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.channel_id != DELETE_WATCH_CHANNEL_ID:
            return
        kept = self.files.pop(payload.message_id, None) or []
        self.files_size -= sum(len(b) for _, b, _ in kept)
        msg = payload.cached_message
        if msg is not None and msg.author.bot:
            return   # 봇 메시지(안내 카드 등)는 기록 안 함
        where = f'<#{payload.channel_id}>'
        if msg is None:
            await self._send(_embed(None, C_DELETE,
                                    f'🗑️ 메시지가 삭제됐어요 {where}\n**내용**\n> 내용을 알 수 없어요 (봇 시작 전 메시지)'))
            return
        content = msg.content or ''
        if len(content) > 1500:
            content = content[:1500] + ' …'
        body = '\n'.join(f'> {ln}' for ln in content.split('\n')) if content else '> (글 내용 없음)'
        files = []
        if msg.attachments:
            have = {fn for fn, _, _ in kept}
            lines = []
            for a in msg.attachments[:10]:
                note = '' if a.filename in have else (' (용량이 커서 보관 못 함)' if a.size > FILE_MAX
                                                      else ' (보관 못 함)')
                lines.append(f'- {discord.utils.escape_markdown(a.filename)}{note}')
            body += '\n**첨부**' + (' — 아래 파일로 다시 올렸어요' if kept else '') + '\n' + '\n'.join(lines)
            files = [discord.File(io.BytesIO(b), filename=fn, spoiler=sp) for fn, b, sp in kept]
        await self._send(_embed(msg.author, C_DELETE,
                                f'🗑️ {_who(msg.author)} 님의 메시지가 삭제됐어요 {where}\n**내용**\n{body}',
                                footer=f'작성 {_hm(msg.created_at)}'), files=files)

    # ── 프로필 사진 / 이름 변경 ─────────────────────────────
    def _in_guild(self, user):
        ch = self.bot.get_channel(LOG_CHANNEL_ID)
        return ch is not None and ch.guild.get_member(user.id) is not None

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        '''기본 프로필 사진 · 사용자명 · 표시 이름 (서버와 무관한 계정 정보)'''
        if after.bot or not self._in_guild(after):
            return
        member = self.bot.get_channel(LOG_CHANNEL_ID).guild.get_member(after.id)
        if before.avatar != after.avatar:
            await self._avatar_log(member, before.display_avatar.url, after.display_avatar.url, '프로필 사진')
        changes = []
        if before.name != after.name:
            changes.append(('사용자명', before.name, after.name))
        if before.global_name != after.global_name:
            changes.append(('표시 이름', before.global_name, after.global_name))
        if changes:
            await self._name_log(member, changes)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        '''서버 닉네임 · 서버 전용 프로필 사진'''
        if after.bot or after.guild.get_channel(LOG_CHANNEL_ID) is None:
            return
        if before.guild_avatar != after.guild_avatar:
            await self._avatar_log(after, (before.guild_avatar or before.avatar or before.default_avatar).url,
                                   after.display_avatar.url, '서버 프로필 사진')
        if before.nick != after.nick:
            await self._name_log(after, [('서버 닉네임', before.nick, after.nick)])

    async def _avatar_log(self, member, before_url, after_url, label):
        await self._send(_embed(member, C_PROFILE,
                                f'🖼️ {_who(member)} 님이 {label}을 바꿨어요\n'
                                f'**{label}**\n[이전]({before_url}) → [변경 후]({after_url})',
                                thumb=after_url))

    async def _name_log(self, member, changes):
        lines = [f'✏️ {_who(member)} 님이 이름을 바꿨어요']
        for label, old, new in changes:
            lines.append(f'**{label}**\n{discord.utils.escape_markdown(old or "(없음)")} → '
                         f'{discord.utils.escape_markdown(new or "(없음)")}')
        await self._send(_embed(member, C_PROFILE, '\n'.join(lines)))


async def setup(bot):
    await bot.add_cog(ServerLog(bot))
