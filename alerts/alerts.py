'''
* 관리 알림 — 관리 채널(ALERT_CHANNEL_ID)에 카드로 남기는 공용 알림
*   ⚠️ 경고 3회 누적 (주간 경고 정산 후)
*   ⏰ 입장 대기 방치 (입장 카드가 24시간 넘게 미승인) — 관리 채널 카드 + 길마·서마 전원 개인 DM
* date   2026.11.02
'''
import time

import discord

from config.guild_config import get_role_id

ALERT_CHANNEL_ID = 1415856432888414298
_WARN = discord.Colour(0xED4245)
_WAIT = discord.Colour(0xFAA61A)


def _esc(s):
    return discord.utils.escape_markdown(s)


def alert_guild(bot):
    '''관리 채널이 있는 길드 (배치처럼 interaction 이 없을 때)'''
    ch = bot.get_channel(ALERT_CHANNEL_ID)
    return ch.guild if ch is not None else None


async def _channel(guild):
    try:
        return guild.get_channel(ALERT_CHANNEL_ID) or await guild.fetch_channel(ALERT_CHANNEL_ID)
    except Exception as e:
        print(f'[alerts] 관리 채널 조회 실패: {e}')
        return None


def _card(colour, title, sub, body, footer, link=None):
    v = discord.ui.LayoutView(timeout=None)
    items = [discord.ui.TextDisplay(f'## {title}\n-# {sub}'), discord.ui.Separator(),
             discord.ui.TextDisplay(body), discord.ui.Separator(), discord.ui.TextDisplay(f'-# {footer}')]
    if link:
        row = discord.ui.ActionRow()
        row.add_item(discord.ui.Button(label=link[0], url=link[1], style=discord.ButtonStyle.link))
        items.append(row)
    v.add_item(discord.ui.Container(*items, accent_colour=colour))
    return v


# ── 경고 3회 누적 ───────────────────────────────────────────
def warning_card(reached, guild, period=''):
    lines = []
    for name, did, warn in reached:
        m = guild.get_member(int(did)) if str(did).isdigit() else None
        who = f'**{_esc(name)}**' + (f' ({_esc(m.display_name)})' if m and m.display_name != name else '')
        lines.append(f'- {who} — 누적 **{warn}회**')
    return _card(_WARN, '⚠️ 경고 3회 누적', '주간 경고 정산에서 경고가 3회에 도달한 길드원이에요',
                 '\n'.join(lines)[:3500], f'<t:{int(time.time())}:f> 정산' + (f' · 기준 {period}' if period else ''))


async def post_warning_alert(guild, reached, period=''):
    if not reached or guild is None:
        return
    ch = await _channel(guild)
    if ch is None:
        return
    try:
        await ch.send(view=warning_card(reached, guild, period), allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        print(f'[alerts] 경고 알림 실패: {e}')


# ── 입장 대기 방치 ──────────────────────────────────────────
def waiting_card(member, joined_ts, card_url):
    return _card(_WAIT, '⏰ 입장 승인 대기 중', '입장 카드가 24시간 넘게 승인되지 않았어요',
                 f'**{_esc(member.display_name)}** ({_esc(member.name)}) 님이 승인을 기다리고 있어요.\n'
                 f'입장 카드에서 **길드원 승인** 또는 **게스트 승인**을 눌러주세요.',
                 f'입장 <t:{joined_ts}:f> · <t:{joined_ts}:R>', link=('입장 카드로 이동', card_url))


def _staff(guild):
    ids = [get_role_id(guild.id, k) for k in ('길마', '서마')]
    roles = [guild.get_role(i) for i in ids if i]
    seen, out = set(), []
    for r in roles:
        for m in (r.members if r else []):
            if not m.bot and m.id not in seen:
                seen.add(m.id)
                out.append(m)
    return out


async def post_waiting_alert(guild, member, joined_ts, card_url):
    '''관리 채널 카드 + 길마·서마 전원 개인 DM. 반환: (DM 성공 수, 실패 수)'''
    ch = await _channel(guild)
    if ch is not None:
        try:
            await ch.send(view=waiting_card(member, joined_ts, card_url),
                          allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            print(f'[alerts] 입장 대기 카드 실패: {e}')
    ok = fail = 0
    for admin in _staff(guild):
        try:
            await admin.send(view=waiting_card(member, joined_ts, card_url))
            ok += 1
        except Exception:
            fail += 1   # DM 차단 등
    return ok, fail
