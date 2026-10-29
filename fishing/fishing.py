'''
* 테일즈런너 낚시 경험치 계산기 (Components V2 카드)
*   - /낚시계산기설치 <채널> (관리자·길마·서마): 채널에 모두가 보는 계산기 카드를 설치 (재시작 후에도 동작)
*   - 설치된 카드 하나를 모두가 같이 씀 (누가 고치든 그 카드가 바뀜). 입력값은 메모리에만 — 재시작 시 빈 계산기부터
*   - 게임에선 현재 레벨의 퍼센트만 보이므로: [색깔] [윙] [퍼센트] + 획득 경험치들 → 도착 계급/퍼센트
*   - 입력 카드: 현재 위치 · 획득 경험치 목록(항목별 ✕) · 색깔/윙 선택 · [퍼센트 입력][경험치 추가][계산][초기화]
*   - 퍼센트·경험치 입력은 팝업(모달) — 디스코드 카드에는 입력창을 직접 둘 수 없음
*   - 결과 카드: 시작 → 도착 · 진행 바 · 다음 레벨까지 남은 경험치 · [경험치 더 추가][초기화]
*   - 레벨표: 공식 홈페이지 게임 레벨 안내(tr.rhaon.co.kr/archive/guide/87)의 누적 필요 경험치
*   - 퍼센트는 게임처럼 소수 둘째 자리 버림. 게임 표시가 둘째 자리까지라 고레벨일수록 오차 있음
* date   2026.10.29
'''
import re
from bisect import bisect_right
from decimal import Decimal

import discord
from discord import app_commands
from discord.ext import commands

from config.guild_config import get_setting, set_setting
from roleSetting.roleSetting import has_role

_ACCENT = discord.Colour(0x3FB8F3)   # 길드 대표색 (다른 카드와 통일)
_RESULT = discord.Colour(0x639922)   # 결과 카드 초록
MAX_ENTRIES = 8                      # 카드 한 장 컴포넌트 40개 제한 (항목당 3개 사용)

# 계급 = 색깔 7단계 × 윙 18종 (레벨 1~126)
COLORS = [('빨간', '🔴'), ('주황', '🟠'), ('노란', '🟡'), ('초록', '🟢'),
          ('파란', '🔵'), ('남색', '🔷'), ('보라', '🟣')]
WINGS = ['병아리', '발바닥', '양말', '슬리퍼', '운동화', '윙 부츠', '데빌 윙 부츠', '엔젤 윙 부츠',
         '파워 윙 부츠', '썬더 윙 부츠', '루나 윙 부츠', '쏠라 윙 부츠', '스텔라 윙 부츠',
         '갤럭시 윙 부츠', '홀리 윙 부츠', '프라우드 윙 부츠', '크로노스 윙 부츠', '인페르노 윙 부츠']

# 레벨별 누적 필요 경험치 (CUM[0] = Lv.1)
CUM = [
    0, 25, 75, 150, 250, 350, 450,
    600, 800, 1000, 1200, 1400, 1600, 1800,
    2000, 2500, 3000, 3750, 4500, 5500, 6500,
    7500, 9500, 11500, 14500, 17500, 21500, 25500,
    29500, 34500, 39500, 47000, 54500, 69500, 84500,
    99500, 124500, 149500, 199500, 249500, 299500, 399500,
    499500, 649500, 799500, 999500, 1199500, 1433700, 1683700,
    2283700, 2883700, 3483700, 4083700, 4683700, 5283700, 5883700,
    7983700, 10083700, 12183700, 14283700, 16383700, 18483700, 20583700,
    25383700, 30183700, 34983700, 39783700, 44583700, 49383700, 54183700,
    64983700, 75783700, 86583700, 97383700, 108183700, 118983700, 129783700,
    153783700, 177783700, 201783700, 225783700, 249783700, 273783700, 297783700,
    345783700, 393783700, 441783700, 489783700, 537783700, 585783700, 633783700,
    729783700, 825783700, 921783700, 1017783700, 1113783700, 1209783700, 1305783700,
    1545783700, 1785783700, 2025783700, 2265783700, 2505783700, 2745783700, 2985783700,
    3585783700, 4185783700, 4785783700, 5385783700, 5985783700, 6585783700, 7185783700,
    9341518810, 10555916255, 11928185368, 13478849466, 15231099897, 17211142883, 19448591458,
    25283168896, 28569980852, 32284078363, 36481008550, 41223539662, 46582599818, 52638337794,
]
MAX_LV = len(CUM)
assert MAX_LV == len(COLORS) * len(WINGS)

_PCT_RE = re.compile(r'^\d{1,2}(\.\d{1,2})?$')


def rank_name(lv):
    c, w = (lv - 1) % len(COLORS), (lv - 1) // len(COLORS)
    return f'{COLORS[c][1]} {COLORS[c][0]} {WINGS[w]}'


def calc(start_lv, pct, gained):
    '''(시작 레벨, 퍼센트 Decimal, 획득 합계) → (도착 레벨, 도착 퍼센트 Decimal|None, 다음 레벨까지 남은 양|None)'''
    if start_lv >= MAX_LV:
        start = CUM[-1]
    else:
        start = CUM[start_lv - 1] + int((CUM[start_lv] - CUM[start_lv - 1]) * pct / 100)
    total = start + gained
    lv = bisect_right(CUM, total)
    if lv >= MAX_LV:
        return MAX_LV, None, None
    base, gap = CUM[lv - 1], CUM[lv] - CUM[lv - 1]
    end_pct = Decimal((total - base) * 10000 // gap) / 100   # 둘째 자리 버림
    return lv, end_pct, CUM[lv] - total


def _bar(pct, width=10):
    '''네모 이모지 진행 바 — ▰▱ 는 폰 글꼴에서 깨져 보임'''
    n = int(pct * width // 100)
    return '🟩' * n + '⬜' * (width - n)



class Calc:
    '''카드 한 장의 입력 상태 (메시지 ID 별)'''
    def __init__(self):
        self.color = self.wing = self.pct = None
        self.entries = []
        self.mode = 'input'

    @property
    def level(self):
        if self.color is None or self.wing is None:
            return None
        return self.wing * len(COLORS) + self.color + 1


_STATES = {}   # 메시지 ID -> Calc


def _st(interaction):
    return _STATES.setdefault(interaction.message.id, Calc())


async def _refresh(interaction, st):
    await interaction.response.edit_message(view=FishingView(st))


# ── 팝업 ─────────────────────────────────────────────
class PercentModal(discord.ui.Modal, title='퍼센트 입력'):
    value = discord.ui.TextInput(label='현재 레벨 경험치 퍼센트', placeholder='77.46', max_length=6)

    def __init__(self, st):
        super().__init__()
        self.st = st
        if st.pct is not None:
            self.value.default = f'{st.pct:.2f}'

    async def on_submit(self, interaction):
        s = self.value.value.strip().replace('%', '').replace(',', '.')
        if not _PCT_RE.match(s):
            await interaction.response.send_message('퍼센트는 0~99.99 사이로 입력하세요. 예) 77.46', ephemeral=True)
            return
        self.st.pct = Decimal(s)
        self.st.mode = 'input'
        await _refresh(interaction, self.st)


class ExpModal(discord.ui.Modal, title='경험치 추가'):
    value = discord.ui.TextInput(label='획득 경험치', placeholder='10000000', max_length=15)

    def __init__(self, st):
        super().__init__()
        self.st = st

    async def on_submit(self, interaction):
        s = re.sub(r'[,\s]', '', self.value.value)
        if not s.isdigit() or int(s) <= 0:
            await interaction.response.send_message('경험치는 1 이상의 숫자로 입력하세요. 예) 10000000', ephemeral=True)
            return
        if len(self.st.entries) >= MAX_ENTRIES:
            await interaction.response.send_message(f'경험치는 {MAX_ENTRIES}건까지 넣을 수 있어요.', ephemeral=True)
            return
        self.st.entries.append(int(s))
        self.st.mode = 'input'
        await _refresh(interaction, self.st)


# ── 카드 컴포넌트 (모두 고정 custom_id — 재시작 후에도 동작) ──────────────
class _ColorSelect(discord.ui.Select):
    def __init__(self, st):
        super().__init__(placeholder='색깔 선택', custom_id='fishcalc:color', options=[
            discord.SelectOption(label=name, value=str(i), emoji=emoji, default=st.color == i)
            for i, (name, emoji) in enumerate(COLORS)])

    async def callback(self, interaction):
        st = _st(interaction)
        st.color = int(self.values[0])
        await _refresh(interaction, st)


class _WingSelect(discord.ui.Select):
    def __init__(self, st):
        super().__init__(placeholder='윙 선택', custom_id='fishcalc:wing', options=[
            discord.SelectOption(label=name, value=str(i), default=st.wing == i,
                                 description=f'Lv.{i * 7 + 1}~{i * 7 + 7}')
            for i, name in enumerate(WINGS)])

    async def callback(self, interaction):
        st = _st(interaction)
        st.wing = int(self.values[0])
        await _refresh(interaction, st)


class _Btn(discord.ui.Button):
    '''누르면 handler(interaction, 카드 상태)를 호출하는 버튼'''
    def __init__(self, handler, name, **kw):
        super().__init__(custom_id=f'fishcalc:{name}', **kw)
        self.handler = handler

    async def callback(self, interaction):
        await self.handler(interaction, _st(interaction))


# 경험치 항목 ✕ — 항목 수가 바뀌므로 DynamicItem (bot.add_dynamic_items 로 등록)
class RemoveButton(discord.ui.DynamicItem[discord.ui.Button], template=r'fishcalc:rm:(?P<i>[0-9]+)'):
    def __init__(self, idx: int):
        super().__init__(discord.ui.Button(
            label='✕', style=discord.ButtonStyle.secondary, custom_id=f'fishcalc:rm:{idx}'))
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['i']))

    async def callback(self, interaction):
        st = _st(interaction)
        if self.idx < len(st.entries):
            st.entries.pop(self.idx)
        await _refresh(interaction, st)


# ── 버튼 동작 ──
async def _ask_pct(interaction, st):
    await interaction.response.send_modal(PercentModal(st))


async def _ask_exp(interaction, st):
    if len(st.entries) >= MAX_ENTRIES:
        await interaction.response.send_message(
            f'경험치는 한 번에 {MAX_ENTRIES}건까지 넣을 수 있어요. 몇 건을 합쳐서 넣어주세요.', ephemeral=True)
        return
    await interaction.response.send_modal(ExpModal(st))


async def _calculate(interaction, st):
    missing = [n for n, ok in (('색깔', st.color is not None), ('윙', st.wing is not None),
                               ('퍼센트', st.pct is not None), ('경험치', bool(st.entries))) if not ok]
    if missing:
        await interaction.response.send_message(f'{", ".join(missing)}을(를) 먼저 입력하세요.', ephemeral=True)
        return
    st.mode = 'result'
    await _refresh(interaction, st)


async def _reset(interaction, st):
    st.__init__()
    await _refresh(interaction, st)


class FishingView(discord.ui.LayoutView):
    '''채널에 설치하는 공용 계산기 카드 — 상태(st)를 그려서 보여줌. 인자 없으면 빈 계산기'''
    def __init__(self, st=None):
        super().__init__(timeout=None)
        st = st or Calc()
        self.add_item(self._result(st) if st.mode == 'result' else self._input(st))

    # 1) 입력 카드
    @staticmethod
    def _input(st):
        where = rank_name(st.level) if st.level else '-# 아래에서 색깔과 윙을 골라주세요'
        pct = f' · **{st.pct:.2f}%**' if st.pct is not None else ' · 퍼센트 미입력'
        items = [
            discord.ui.TextDisplay('### 🎣 낚시 경험치 계산기\n-# 레벨과 퍼센트를 고르고 경험치를 쌓아보세요'),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f'**현재 위치**\n{where}{pct if st.level else ""}'),
        ]
        if st.entries:
            items.append(discord.ui.TextDisplay(f'**획득 경험치 · {len(st.entries)}건**'))
            for i, exp in enumerate(st.entries):
                items.append(discord.ui.Section(discord.ui.TextDisplay(f'🐟 {exp:,}'), accessory=RemoveButton(i)))
            items.append(discord.ui.TextDisplay(f'**합계** {sum(st.entries):,}'))
        else:
            items.append(discord.ui.TextDisplay('**획득 경험치**\n-# [경험치 추가]로 한 건씩 넣어주세요'))

        selects = [discord.ui.ActionRow(_ColorSelect(st)), discord.ui.ActionRow(_WingSelect(st))]
        buttons = discord.ui.ActionRow(
            _Btn(_ask_pct, 'pct', label='퍼센트 입력', emoji='📊', style=discord.ButtonStyle.secondary),
            _Btn(_ask_exp, 'exp', label='경험치 추가', emoji='➕', style=discord.ButtonStyle.secondary),
            _Btn(_calculate, 'calc', label='계산', emoji='🧮', style=discord.ButtonStyle.primary),
            _Btn(_reset, 'reset', label='초기화', emoji='🔄', style=discord.ButtonStyle.danger),
        )
        return discord.ui.Container(*items, discord.ui.Separator(), *selects, buttons, accent_colour=_ACCENT)

    # 3) 결과 카드
    @staticmethod
    def _result(st):
        gained = sum(st.entries)
        start_lv, start_pct = st.level, st.pct
        end_lv, end_pct, remain = calc(start_lv, start_pct, gained)
        start_txt = f'{rank_name(start_lv)} · **{start_pct:.2f}%**' if start_lv < MAX_LV else rank_name(start_lv)

        if end_pct is None:
            end_txt = f'{rank_name(end_lv)} 👑'
            progress = '👑 최고 레벨에 도달했어요'
        else:
            end_txt = f'{rank_name(end_lv)} · **{end_pct:.2f}%**'
            up = end_lv - start_lv
            change = f'⬆️ {up}레벨 상승' if up else f'⬆️ +{end_pct - start_pct:.2f}%'
            progress = (f'{_bar(end_pct)} {end_pct:.2f}%\n{change}\n'
                        f'🚩 {rank_name(end_lv + 1)}까지 **{remain:,}** 남음')

        buttons = discord.ui.ActionRow(
            _Btn(_ask_exp, 'exp', label='경험치 더 추가', emoji='➕', style=discord.ButtonStyle.secondary),
            _Btn(_reset, 'reset', label='초기화', emoji='🔄', style=discord.ButtonStyle.danger),
        )
        return discord.ui.Container(
            discord.ui.TextDisplay(f'### 🏆 계산 결과\n-# 경험치 {len(st.entries)}건 · 합계 {gained:,}'),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f'-# 시작\n{start_txt}'),
            discord.ui.TextDisplay('-# ↓'),   # 이모지만 있는 줄은 크게 표시돼서 작은 글자 화살표
            discord.ui.TextDisplay(f'-# 도착\n{end_txt}'),
            discord.ui.Separator(),
            discord.ui.TextDisplay(progress),
            discord.ui.Separator(),
            buttons,
            accent_colour=_RESULT,
        )


def _is_admin(interaction):
    return (interaction.user.guild_permissions.administrator
            or has_role(interaction, '길마') or has_role(interaction, '서마'))


@app_commands.guild_only()
class Fishing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(FishingView())   # 설치된 공용 카드 — 재시작 후에도 버튼/선택 동작
        self.bot.add_dynamic_items(RemoveButton)

    # 대시보드처럼 채널에 상주 — 이전 설치분은 지우고 새로 게시
    @app_commands.command(name='낚시계산기설치', description='(관리자) 선택한 채널에 낚시 경험치 계산기 카드 설치')
    @app_commands.describe(channel='계산기 카드를 설치할 채널')
    async def install(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not _is_admin(interaction):
            await interaction.response.send_message('관리자만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        old_ch = get_setting(interaction.guild.id, '_fish_ch')
        old_msg = get_setting(interaction.guild.id, '_fish_msg')
        if old_ch and old_msg:
            try:
                ch = (interaction.guild.get_channel(int(old_ch))
                      or await self.bot.fetch_channel(int(old_ch)))
                old = await ch.fetch_message(int(old_msg))
                await old.delete()
            except Exception:
                pass
        try:
            msg = await channel.send(view=FishingView())
        except discord.Forbidden:
            await interaction.followup.send(
                f'{channel.mention} 에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인하세요.', ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f'설치 실패: {e}', ephemeral=True)
            return
        set_setting(interaction.guild.id, '_fish_ch', channel.id)
        set_setting(interaction.guild.id, '_fish_msg', msg.id)
        await interaction.followup.send(f'{channel.mention} 에 낚시 계산기를 설치했습니다.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(Fishing(bot))
