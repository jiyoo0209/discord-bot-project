'''
* 연말 결산 — /연말결산 [연도] (길마 전용, 본인만 보임). 디자인용 원본 데이터를 정리해서 보여줌
*   별도 시트·기록 없이 이미 있는 데이터만 읽음:
*     💬 채팅  : 봇이 읽을 수 있는 모든 채팅 채널(+스레드)의 그해 메시지를 처음부터 읽음
*                → 채팅왕 · 리액션 부자 · 사진작가상 · 최애 서버 이모지 TOP3 · 시간대/요일 · 올해 첫 메시지
*     🎙️ 통화  : 로그 채널(serverlog)의 📥입장 / 🔀이동 / 📤퇴장 카드로 통화 구간을 복원
*                → 통화왕 · 마라톤상 · 올빼미상(0~6시) · 찰떡 듀오(같은 방에 함께 있던 시간)
*     🎮 게임  : 임시채널생성 채널의 음성방 카드 → 올해의 게임(방 이름별 생성 횟수 · 사용 시간)
*   결과: 요약 메시지 + 전체 데이터 .txt / .json 첨부 (15분이 넘게 걸리면 DM 으로 보냄)
* date   2026.11.18
'''
import io
import re
import json
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from roleSetting.roleSetting import has_role
from util.dates import KST
from serverlog.serverlog import LOG_CHANNEL_ID
from voice.voice import WATCH_CHANNEL_ID as ROOM_CHANNEL_ID, _card_info, _walk

TOP = 10                       # 순위 표시 인원
OWL_START, OWL_END = 0, 6      # 올빼미 시간대 (KST 0시 ~ 6시)
WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일']
_EMOJI_RE = re.compile(r'<a?:\w+:(\d+)>')
_IMG_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.bmp')


def _kst(dt):
    return dt.astimezone(KST)


def _hm(minutes):
    m = int(round(minutes))
    return f'{m // 60}시간 {m % 60}분' if m >= 60 else f'{m}분'


def _is_image(a):
    return (a.content_type or '').startswith('image/') or a.filename.lower().endswith(_IMG_EXT)


# ──────────────────────────────────────────────────────────────
# 💬 채팅
# ──────────────────────────────────────────────────────────────
async def _readable_channels(guild):
    me = guild.me
    chans = [c for c in list(guild.text_channels) + list(guild.voice_channels) + list(guild.stage_channels)
             if c.permissions_for(me).read_message_history and c.permissions_for(me).view_channel]
    threads = list(guild.threads)
    for c in guild.text_channels:   # 보관된 공개 스레드도 포함
        if not c.permissions_for(me).read_message_history:
            continue
        try:
            async for t in c.archived_threads(limit=None):
                threads.append(t)
        except Exception:
            pass
    return chans + [t for t in threads if t.permissions_for(me).read_message_history]


async def chat_stats(guild, start, end, progress=None):
    emoji_ids = {e.id: e for e in guild.emojis}
    msgs, imgs, reacts = Counter(), Counter(), Counter()
    emoji_use, hours, wdays = Counter(), Counter(), Counter()
    names = {}
    first = None          # (작성시각, 이름, 내용, 링크)
    best = None           # (리액션 수, 이름, 내용, 링크)
    total, skipped = 0, []
    channels = await _readable_channels(guild)
    for idx, ch in enumerate(channels, 1):
        try:
            async for m in ch.history(limit=None, after=start, before=end, oldest_first=True):
                if m.author.bot or m.type not in (discord.MessageType.default, discord.MessageType.reply):
                    continue
                uid = m.author.id
                names[uid] = m.author.display_name
                total += 1
                msgs[uid] += 1
                t = _kst(m.created_at)
                hours[t.hour] += 1
                wdays[t.weekday()] += 1
                imgs[uid] += sum(1 for a in m.attachments if _is_image(a))
                for eid in _EMOJI_RE.findall(m.content or ''):
                    if int(eid) in emoji_ids:
                        emoji_use[int(eid)] += 1
                rc = 0
                for r in m.reactions:
                    rc += r.count
                    if getattr(r.emoji, 'id', None) in emoji_ids:
                        emoji_use[r.emoji.id] += r.count
                reacts[uid] += rc
                if rc and (best is None or rc > best[0]):
                    best = (rc, m.author.display_name, (m.content or '(첨부만 있는 메시지)')[:200], m.jump_url)
                if first is None or m.created_at < first[0]:
                    first = (m.created_at, m.author.display_name, m.content or '(첨부만 있는 메시지)', m.jump_url)
        except discord.Forbidden:
            skipped.append(f'#{ch.name}')
        except Exception as e:
            skipped.append(f'#{ch.name} ({e})')
        if progress and idx % 5 == 0:
            await progress(f'💬 채팅 읽는 중… 채널 {idx}/{len(channels)} · 메시지 {total:,}개')
    return {
        'total': total, 'names': names, 'msgs': msgs, 'imgs': imgs, 'reacts': reacts,
        'emoji_use': emoji_use, 'emoji_obj': emoji_ids, 'hours': hours, 'wdays': wdays,
        'first': first, 'best': best, 'channels': len(channels), 'skipped': skipped,
    }


# ──────────────────────────────────────────────────────────────
# 🎙️ 통화 — 로그 카드에서 (사람, 방, 시작, 끝) 구간 복원
# ──────────────────────────────────────────────────────────────
_BOLD = re.compile(r'\*\*(.+?)(?<!\\)\*\*')
_VC_NAME = re.compile(r'🔊 `([^`]*)`|<#(\d+)>')
_DUR = re.compile(r'머문 시간 (?:(\d+)시간)?\s*(?:(\d+)분)?')


def _unesc(s):
    return re.sub(r'\\(.)', r'\1', s)


def _channels_in(text, guild):
    out = []
    for name, cid in _VC_NAME.findall(text):
        if name:
            out.append(name)
        else:
            c = guild.get_channel(int(cid))
            out.append(c.name if c else '(삭제된 채널)')
    return out


def _parse_dur(footer):
    m = _DUR.search(footer or '')
    if not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


async def voice_sessions(guild, start, end, progress=None):
    ch = guild.get_channel(LOG_CHANNEL_ID)
    if ch is None:
        return [], {}, '로그 채널을 찾을 수 없어요'
    events = []   # (시각, 사용자명, 표시이름, 종류, [방...], 퇴장 시 머문분)
    n = 0
    async for msg in ch.history(limit=None, after=start, before=end, oldest_first=True):
        n += 1
        if progress and n % 2000 == 0:
            await progress(f'🎙️ 통화 기록 읽는 중… 로그 {n:,}개')
        if msg.author.id != guild.me.id or not msg.embeds:
            continue
        e = msg.embeds[0]
        d = e.description or ''
        kind = 'join' if d.startswith('📥') else 'leave' if d.startswith('📤') else 'move' if d.startswith('🔀') else None
        if not kind or not e.author or not e.author.name:
            continue
        b = _BOLD.search(d)
        disp = _unesc(b.group(1)) if b else e.author.name
        when = e.timestamp or msg.created_at
        events.append((when, e.author.name, disp, kind, _channels_in(d, guild), _parse_dur(e.footer.text if e.footer else '')))
    # 사람별로 구간 만들기
    names, sessions, open_ = {}, [], {}
    for when, user, disp, kind, chans, dur in events:
        names[user] = disp
        if kind == 'join' and chans:
            open_[user] = (chans[0], when)
        elif kind == 'move' and len(chans) >= 2:
            if user in open_:
                c, s = open_[user]
                sessions.append((user, c, s, when))
            open_[user] = (chans[1], when)
        elif kind == 'leave':
            if user in open_:
                c, s = open_.pop(user)
                sessions.append((user, c, s, when))
            elif dur is not None and chans:            # 입장 기록이 없으면 '머문 시간' 으로 시작 추정
                sessions.append((user, chans[0], when - timedelta(minutes=dur), when))
    now = discord.utils.utcnow()
    live = {m.name for vc in guild.voice_channels for m in vc.members}
    for user, (c, s) in open_.items():                # 지금도 통화 중이면 지금까지로 계산
        if user in live:
            sessions.append((user, c, s, min(now, end)))
    return sessions, names, None


def voice_stats(sessions):
    total = Counter()
    longest = []                           # (분, 사람, 방, 시작)
    owl = Counter()
    rooms = Counter()
    by_room = defaultdict(list)
    for user, room, s, e in sessions:
        mins = max(0.0, (e - s).total_seconds() / 60)
        total[user] += mins
        rooms[room] += mins
        longest.append((mins, user, room, s))
        by_room[room].append((user, s, e))
        # 올빼미: KST 0~6시와 겹치는 시간
        day = _kst(s).replace(hour=0, minute=0, second=0, microsecond=0)
        while day <= _kst(e):
            a, b = day + timedelta(hours=OWL_START), day + timedelta(hours=OWL_END)
            ov = (min(_kst(e), b) - max(_kst(s), a)).total_seconds()
            if ov > 0:
                owl[user] += ov / 60
            day += timedelta(days=1)
    duo = Counter()
    for room, items in by_room.items():
        items.sort(key=lambda x: x[1])
        for i, (u1, s1, e1) in enumerate(items):
            for u2, s2, e2 in items[i + 1:]:
                if s2 >= e1:
                    break
                if u1 == u2:
                    continue
                ov = (min(e1, e2) - max(s1, s2)).total_seconds()
                if ov > 0:
                    duo[tuple(sorted((u1, u2)))] += ov / 60
    longest.sort(reverse=True)
    return {'total': total, 'longest': longest, 'owl': owl, 'duo': duo, 'rooms': rooms}


# ──────────────────────────────────────────────────────────────
# 🎮 올해의 게임 — 임시 음성방 카드
# ──────────────────────────────────────────────────────────────
_RANGE = re.compile(r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}) ~ (\d{4}/\d{2}/\d{2} \d{2}:\d{2})')


async def game_stats(guild, start, end):
    ch = guild.get_channel(ROOM_CHANNEL_ID)
    if ch is None:
        return Counter(), Counter(), '임시채널생성 채널을 찾을 수 없어요'
    count, minutes, label = Counter(), Counter(), {}
    async for msg in ch.history(limit=None, after=start, before=end):
        if msg.author.id != guild.me.id:
            continue
        _cid, name, _c, _a = _card_info(msg)
        if not name:
            continue
        key = ' '.join(name.split()).lower()
        label.setdefault(key, name)
        count[key] += 1
        texts = [e.description or '' for e in msg.embeds]
        texts += [c.content for c in _walk(msg.components) if getattr(c, 'content', None)]
        for t in texts:
            m = _RANGE.search(t)
            if m:
                a, b = (datetime.strptime(x, '%Y/%m/%d %H:%M') for x in m.groups())
                minutes[key] += max(0, (b - a).total_seconds() / 60)
                break
    return Counter({label[k]: v for k, v in count.items()}), Counter({label[k]: v for k, v in minutes.items()}), None


# ──────────────────────────────────────────────────────────────
# 리포트
# ──────────────────────────────────────────────────────────────
def _rank(counter, fmt, name_of=lambda k: k, n=TOP):
    rows = [f'{i}. {name_of(k)} — {fmt(v)}' for i, (k, v) in enumerate(counter.most_common(n), 1) if v > 0]
    return rows or ['(데이터 없음)']


def build_report(year, chat, voice, vnames, games, game_min, notes):
    cn = lambda uid: chat['names'].get(uid, str(uid))
    vn = lambda user: vnames.get(user, user)
    L = [f'# 🌸 마커 {year} 연말 결산 데이터', '']
    # 통화
    L += ['## 🎙️ 통화왕 (총 통화 시간)'] + _rank(voice['total'], _hm, vn) + ['']
    L += ['## 🏃 마라톤상 (한 번에 가장 오래)']
    L += [f'{i}. {vn(u)} — {_hm(m)} · 🔊 {room} · {_kst(s):%m/%d %H:%M} 시작'
          for i, (m, u, room, s) in enumerate(voice['longest'][:TOP], 1)] or ['(데이터 없음)']
    L += ['', '## 🦉 올빼미상 (0~6시 통화 시간)'] + _rank(voice['owl'], _hm, vn) + ['']
    L += ['## 💞 찰떡 듀오 (같은 통화방에 함께 있던 시간)'] + _rank(voice['duo'], _hm, lambda p: f'{vn(p[0])} & {vn(p[1])}') + ['']
    L += ['## 🎮 올해의 게임 (임시 음성방 생성 횟수)']
    L += [f'{i}. {g} — {c}회 · 총 {_hm(game_min.get(g, 0))}' for i, (g, c) in enumerate(games.most_common(TOP), 1)] or ['(데이터 없음)']
    L += ['']
    # 채팅
    L += [f'## 💬 채팅왕 (메시지 수 · 전체 {chat["total"]:,}개)'] + _rank(chat['msgs'], lambda v: f'{v:,}개', cn) + ['']
    L += ['## 💖 리액션 부자 (받은 리액션 수)'] + _rank(chat['reacts'], lambda v: f'{v:,}개', cn)
    if chat['best']:
        rc, who, text, url = chat['best']
        L += [f'- 가장 반응 좋았던 메시지: {who} · 리액션 {rc}개 · "{text}" ({url})']
    L += ['', '## 📸 사진작가상 (올린 사진 수)'] + _rank(chat['imgs'], lambda v: f'{v:,}장', cn) + ['']
    L += ['## 😍 최애 이모지 TOP 3 (서버 이모지 · 메시지+리액션)']
    eo = chat['emoji_obj']
    L += [f'{i}. :{eo[e].name}: {str(eo[e])} — {c:,}회' for i, (e, c) in enumerate(chat['emoji_use'].most_common(3), 1)] or ['(데이터 없음)']
    L += ['', '## ⏰ 가장 활발한 시간대 (메시지 수, KST)']
    L += [f'{h:02d}시 — {chat["hours"].get(h, 0):,}' for h in range(24)]
    if chat['hours']:
        h, c = chat['hours'].most_common(1)[0]
        L += [f'→ 1위: {h:02d}시 ({c:,}개)']
    L += ['', '## 📅 가장 활발한 요일 (메시지 수)']
    L += [f'{WEEKDAYS[d]} — {chat["wdays"].get(d, 0):,}' for d in range(7)]
    if chat['wdays']:
        d, c = chat['wdays'].most_common(1)[0]
        L += [f'→ 1위: {WEEKDAYS[d]}요일 ({c:,}개)']
    L += ['', '## 🎉 올해 첫 메시지']
    if chat['first']:
        t, who, text, url = chat['first']
        L += [f'- 작성자: {who}', f'- 작성 시각: {_kst(t):%Y-%m-%d %H:%M:%S}', f'- 내용: {text}', f'- 링크: {url}']
    else:
        L += ['(데이터 없음)']
    L += ['', '## ℹ️ 데이터 범위'] + [f'- {n}' for n in notes]
    return '\n'.join(L)


def build_json(year, chat, voice, vnames, games, game_min):
    cn = lambda uid: chat['names'].get(uid, str(uid))
    vn = lambda user: vnames.get(user, user)
    eo = chat['emoji_obj']
    return {
        'year': year,
        'voice_total_min': [{'name': vn(u), 'minutes': round(m)} for u, m in voice['total'].most_common()],
        'marathon': [{'name': vn(u), 'minutes': round(m), 'channel': r, 'start': _kst(s).isoformat()}
                     for m, u, r, s in voice['longest'][:TOP]],
        'owl_min': [{'name': vn(u), 'minutes': round(m)} for u, m in voice['owl'].most_common() if m > 0],
        'duo_min': [{'names': [vn(p[0]), vn(p[1])], 'minutes': round(m)} for p, m in voice['duo'].most_common(TOP)],
        'games': [{'name': g, 'rooms': c, 'minutes': round(game_min.get(g, 0))} for g, c in games.most_common()],
        'chat_total': chat['total'],
        'chat': [{'name': cn(u), 'messages': c} for u, c in chat['msgs'].most_common()],
        'reactions_received': [{'name': cn(u), 'count': c} for u, c in chat['reacts'].most_common() if c],
        'best_message': dict(zip(('reactions', 'name', 'content', 'url'), chat['best'])) if chat['best'] else None,
        'photos': [{'name': cn(u), 'count': c} for u, c in chat['imgs'].most_common() if c],
        'emoji_top3': [{'name': eo[e].name, 'id': e, 'url': eo[e].url, 'count': c} for e, c in chat['emoji_use'].most_common(3)],
        'hours': {f'{h:02d}': chat['hours'].get(h, 0) for h in range(24)},
        'weekdays': {WEEKDAYS[d]: chat['wdays'].get(d, 0) for d in range(7)},
        'first_message': ({'name': chat['first'][1], 'time': _kst(chat['first'][0]).strftime('%Y-%m-%d %H:%M:%S'),
                           'content': chat['first'][2], 'url': chat['first'][3]} if chat['first'] else None),
    }


class YearEnd(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._running = False

    @app_commands.command(name='연말결산', description='(길마) 올해 통화·채팅·게임 결산 데이터 정리 (본인만 보임)')
    @app_commands.describe(연도='예: 2026 (비우면 올해)')
    @app_commands.guild_only()
    async def yearend(self, interaction: discord.Interaction, 연도: app_commands.Range[int, 2015, 2100] = None):
        if not has_role(interaction, '길마'):
            await interaction.response.send_message('길마만 사용 가능합니다!', ephemeral=True)
            return
        if self._running:
            await interaction.response.send_message('이미 결산을 만들고 있어요. 끝나면 다시 시도해주세요.', ephemeral=True)
            return
        await interaction.response.send_message('📊 결산 데이터를 모으는 중이에요… 채팅이 많으면 몇 분 걸려요.', ephemeral=True)
        self._running = True
        year = 연도 or datetime.now(KST).year
        start = datetime(year, 1, 1, tzinfo=KST)
        end = min(datetime(year + 1, 1, 1, tzinfo=KST), discord.utils.utcnow().astimezone(KST) + timedelta(minutes=1))
        guild = interaction.guild

        async def progress(text):
            try:
                await interaction.edit_original_response(content=f'📊 {text}')
            except Exception:
                pass   # 15분이 지나면 수정 불가 — 조용히 넘어감
        try:
            chat = await chat_stats(guild, start, end, progress)
            await progress('🎙️ 통화 기록 정리 중…')
            sessions, vnames, verr = await voice_sessions(guild, start, end, progress)
            vstat = voice_stats(sessions)
            games, game_min, gerr = await game_stats(guild, start, end)
            first_log = min((s for _u, _r, s, _e in sessions), default=None)
            notes = [f'채팅: {year}년 메시지 {chat["total"]:,}개 · 읽은 채널 {chat["channels"]}개 (봇 메시지 제외)',
                     '통화: 로그 채널 입장/퇴장 기록 기준' + (f' · {_kst(first_log):%Y-%m-%d}부터' if first_log else ' · 기록 없음')
                     + ' (봇 재시작 등으로 빠진 구간은 제외)',
                     '게임: 임시채널생성 채널의 음성방 카드 기준 (방 이름별)',
                     '최애 이모지: 이 서버 커스텀 이모지만 집계']
            if chat['skipped']:
                notes.append('읽지 못한 채널: ' + ', '.join(chat['skipped'][:15]))
            notes += [x for x in (verr, gerr) if x]
            report = build_report(year, chat, vstat, vnames, games, game_min, notes)
            data = build_json(year, chat, vstat, vnames, games, game_min)
            files = lambda: [discord.File(io.BytesIO(report.encode('utf-8')), filename=f'연말결산_{year}.txt'),
                             discord.File(io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')),
                                          filename=f'연말결산_{year}.json')]
            summary = report if len(report) <= 1900 else report[:1850].rsplit('\n', 1)[0] + '\n…(전체는 첨부 파일)'
            try:
                await interaction.followup.send(summary, files=files(), ephemeral=True)
            except Exception:
                await interaction.user.send(summary, files=files())   # 15분 초과 등 → DM 으로
            await progress('✅ 결산 데이터를 정리했어요. 아래 메시지와 첨부 파일을 확인하세요.')
        except Exception as e:
            print(f'[yearend] 실패: {e}')
            await progress(f'⚠️ 결산을 만들지 못했어요: {e}')
        finally:
            self._running = False


async def setup(bot):
    await bot.add_cog(YearEnd(bot))
