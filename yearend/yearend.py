'''
* 연말 결산 — /연말결산 [연도] (길마 전용, 본인만 보임). 디자인용 원본 데이터를 정리해서 보여줌
*   별도 시트·기록 없이 이미 있는 데이터만 읽음:
*     💬 채팅  : 봇이 읽을 수 있는 모든 채팅 채널(+스레드)의 그해 메시지를 처음부터 읽음
*                → 채팅왕 · 리액션 부자 · 사진작가상 · 최애 서버 이모지 TOP3 · 올해 첫 메시지
*     🎙️ 통화  : 로그 채널(serverlog)의 📥입장 / 🔀이동 / 📤퇴장 카드로 통화 구간을 복원
*                → 통화왕 · 마라톤왕 · 올빼미상(0~6시) · 찰떡 듀오 · 시간대/요일 그래프(통화 시간 기준)
*     👤 길드원별 : 총 통화 시간 · 채팅 수 · 통화가 가장 활발한 시간대/요일
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

TOP = 5                        # 순위 표시 인원
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
    names, unames = {}, {}
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
                unames[uid] = m.author.name
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
        'total': total, 'names': names, 'unames': unames, 'msgs': msgs, 'imgs': imgs, 'reacts': reacts,
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
    hours, wdays = Counter(), Counter()
    u_hours, u_wdays = defaultdict(Counter), defaultdict(Counter)
    for user, room, s, e in sessions:
        t, end_ = _kst(s), _kst(e)
        while t < end_:                            # 한 시간 단위로 잘라서 시간대·요일에 분 배분
            nxt = min(end_, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            mm = (nxt - t).total_seconds() / 60
            hours[t.hour] += mm
            wdays[t.weekday()] += mm
            u_hours[user][t.hour] += mm
            u_wdays[user][t.weekday()] += mm
            t = nxt
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
    return {'total': total, 'longest': longest, 'owl': owl, 'duo': duo, 'rooms': rooms,
            'hours': hours, 'wdays': wdays, 'u_hours': u_hours, 'u_wdays': u_wdays}


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


def _top1(counter):
    return counter.most_common(1)[0] if counter and counter.most_common(1)[0][1] > 0 else None


def members(guild, chat, voice, vnames):
    '''길드원별 통계 — 통화 로그는 사용자명, 채팅은 ID 로 모이므로 사용자명으로 합침'''
    chat_by = Counter()
    disp = {}
    for uid, c in chat['msgs'].items():
        un = chat['unames'].get(uid, str(uid))
        chat_by[un] += c
        disp[un] = chat['names'].get(uid, un)
    for un, d in vnames.items():
        disp.setdefault(un, d)
    for m in getattr(guild, 'members', []):   # 지금 서버 닉네임 우선
        if not m.bot and m.name in disp:
            disp[m.name] = m.display_name
    rows = []
    for un in disp:
        h, w = _top1(voice['u_hours'].get(un, Counter())), _top1(voice['u_wdays'].get(un, Counter()))
        rows.append({
            'name': disp[un], 'user': un,
            'voice_min': round(voice['total'].get(un, 0)),
            'messages': chat_by.get(un, 0),
            'top_hour': f'{h[0]:02d}시' if h else None,
            'top_weekday': f'{WEEKDAYS[w[0]]}요일' if w else None,
            'hours_min': {f'{x:02d}': round(voice['u_hours'].get(un, {}).get(x, 0)) for x in range(24)},
            'weekdays_min': {WEEKDAYS[x]: round(voice['u_wdays'].get(un, {}).get(x, 0)) for x in range(7)},
        })
    rows.sort(key=lambda r: (-r['voice_min'], -r['messages'], r['name']))
    return rows


def build_report(year, chat, voice, vnames, games, members_, notes):
    cn = lambda uid: chat['names'].get(uid, str(uid))
    vn = lambda user: vnames.get(user, user)
    L = [f'# 🌸 마커 {year} 연말 결산', '']
    L += ['## 🎙️ 통화왕 (총 통화 시간 TOP 5)'] + _rank(voice['total'], _hm, vn) + ['']
    L += ['## 🏃 마라톤왕 (한 번에 가장 오래 머문 기록)']
    if voice['longest']:
        m, u, room, st = voice['longest'][0]
        L += [f'{vn(u)} — {_hm(m)}  (🔊 {room} · {_kst(st):%m/%d %H:%M} 시작)']
    else:
        L += ['(데이터 없음)']
    L += ['', '## 🦉 올빼미상 (0~6시 통화 시간 TOP 5)'] + _rank(voice['owl'], _hm, vn) + ['']
    L += ['## 💞 찰떡 듀오 (같은 통화방에 함께 있던 시간이 가장 긴 두 사람)']
    d = _top1(voice['duo'])
    L += [f'{vn(d[0][0])} & {vn(d[0][1])}  (함께 {_hm(d[1])})' if d else '(데이터 없음)', '']
    L += ['## 🎮 올해의 게임 (임시 음성방 이름 TOP 5)'] + _rank(games, lambda v: f'{v}회') + ['']
    L += [f'## 💬 채팅왕 ({year}년 메시지 수 TOP 5)'] + _rank(chat['msgs'], lambda v: f'{v:,}회', cn) + ['']
    L += ['## 💖 리액션 부자']
    r = _top1(chat['reacts'])
    L += [f'- 가장 많이 받은 사람: {cn(r[0])} — 리액션 {r[1]:,}개' if r else '- 가장 많이 받은 사람: (데이터 없음)']
    if chat['best']:
        rc, who, text, url = chat['best']
        L += [f'- 가장 반응 좋았던 메시지: {who} · 리액션 {rc}개', f'  "{text}"', f'  {url}']
    L += ['', '## 📸 사진작가상']
    p = _top1(chat['imgs'])
    L += [f'{cn(p[0])} — {p[1]:,}회' if p else '(데이터 없음)', '']
    L += ['## 😍 최애 이모지 TOP 3 (서버 이모지)']
    eo = chat['emoji_obj']
    L += [f'{i}. :{eo[e].name}: {str(eo[e])} — {c:,}회' for i, (e, c) in enumerate(chat['emoji_use'].most_common(3), 1)] or ['(데이터 없음)']
    L += ['', '## 🎉 올해의 첫 메시지']
    if chat['first']:
        t, who, text, url = chat['first']
        L += [f'- 닉네임: {who}', f'- 시각: {_kst(t):%Y-%m-%d %H시 %M분 %S초}', f'- 내용: {text}', f'- 링크: {url}']
    else:
        L += ['(데이터 없음)']
    L += ['', '## ⏰ 가장 활발한 시간대 (통화방 기준 · 시간대별 총 통화 시간)']
    L += [f'{h:02d}시 — {_hm(voice["hours"].get(h, 0))}' for h in range(24)]
    h = _top1(voice['hours'])
    if h:
        L += [f'→ 1위: {h[0]:02d}시']
    L += ['', '## 📅 가장 활발한 요일 (통화방 기준 · 요일별 총 통화 시간)']
    L += [f'{WEEKDAYS[x]} — {_hm(voice["wdays"].get(x, 0))}' for x in range(7)]
    w = _top1(voice['wdays'])
    if w:
        L += [f'→ 1위: {WEEKDAYS[w[0]]}요일']
    L += ['', f'## 👤 길드원별 {year} 연말 정산 (총 통화 시간 · 채팅 수 · 통화 가장 활발한 시간대/요일)']
    L += [f'- {r["name"]} — 통화 {_hm(r["voice_min"])} · 채팅 {r["messages"]:,}회 · '
          f'{r["top_hour"] or "-"} · {r["top_weekday"] or "-"}' for r in members_] or ['(데이터 없음)']
    L += ['', '## ℹ️ 데이터 범위'] + [f'- {n}' for n in notes]
    return '\n'.join(L)


def build_json(year, chat, voice, vnames, games, members_):
    cn = lambda uid: chat['names'].get(uid, str(uid))
    vn = lambda user: vnames.get(user, user)
    eo = chat['emoji_obj']
    m = voice['longest'][0] if voice['longest'] else None
    d, r, p = _top1(voice['duo']), _top1(chat['reacts']), _top1(chat['imgs'])
    return {
        'year': year,
        'call_king': [{'name': vn(u), 'minutes': round(v)} for u, v in voice['total'].most_common(TOP)],
        'marathon': {'name': vn(m[1]), 'minutes': round(m[0]), 'channel': m[2], 'start': _kst(m[3]).strftime('%Y-%m-%d %H:%M')} if m else None,
        'owl': [{'name': vn(u), 'minutes': round(v)} for u, v in voice['owl'].most_common(TOP) if v > 0],
        'duo': {'names': [vn(d[0][0]), vn(d[0][1])], 'minutes': round(d[1])} if d else None,
        'games': [{'name': g, 'count': c} for g, c in games.most_common(TOP)],
        'chat_king': [{'name': cn(u), 'messages': c} for u, c in chat['msgs'].most_common(TOP)],
        'reaction_rich': {'name': cn(r[0]), 'reactions': r[1]} if r else None,
        'best_message': dict(zip(('reactions', 'name', 'content', 'url'), chat['best'])) if chat['best'] else None,
        'photographer': {'name': cn(p[0]), 'count': p[1]} if p else None,
        'emoji_top3': [{'name': eo[e].name, 'id': e, 'url': eo[e].url, 'count': c} for e, c in chat['emoji_use'].most_common(3)],
        'first_message': ({'name': chat['first'][1], 'time': _kst(chat['first'][0]).strftime('%Y-%m-%d %H:%M:%S'),
                           'content': chat['first'][2], 'url': chat['first'][3]} if chat['first'] else None),
        'voice_hours_min': {f'{h:02d}': round(voice['hours'].get(h, 0)) for h in range(24)},
        'voice_weekdays_min': {WEEKDAYS[x]: round(voice['wdays'].get(x, 0)) for x in range(7)},
        'members': members_,
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
            games, _game_min, gerr = await game_stats(guild, start, end)
            mem = members(guild, chat, vstat, vnames)
            first_log = min((s for _u, _r, s, _e in sessions), default=None)
            notes = [f'채팅: {year}년 메시지 {chat["total"]:,}개 · 읽은 채널 {chat["channels"]}개 (봇 메시지 제외)',
                     '통화: 로그 채널 입장/퇴장 기록 기준' + (f' · {_kst(first_log):%Y-%m-%d}부터' if first_log else ' · 기록 없음')
                     + ' (봇 재시작 등으로 빠진 구간은 제외)',
                     '게임: 임시채널생성 채널의 음성방 카드 기준 (방 이름별)',
                     '최애 이모지: 이 서버 커스텀 이모지만 집계']
            if chat['skipped']:
                notes.append('읽지 못한 채널: ' + ', '.join(chat['skipped'][:15]))
            notes += [x for x in (verr, gerr) if x]
            report = build_report(year, chat, vstat, vnames, games, mem, notes)
            data = build_json(year, chat, vstat, vnames, games, mem)
            files = lambda: [discord.File(io.BytesIO(report.encode('utf-8')), filename=f'연말결산_{year}.txt'),
                             discord.File(io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')),
                                          filename=f'연말결산_{year}.json')]
            head = report.split('\n## ⏰')[0]   # 메시지에는 수상 항목만, 그래프·길드원별은 첨부 파일
            summary = (head if len(head) <= 1850 else head[:1850].rsplit('\n', 1)[0]) + '\n\n⏰ 시간대·요일 그래프 / 👤 길드원별 정산은 첨부 파일에 있어요.'
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
