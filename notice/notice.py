'''
* 공지사항 카드 — 채널에 상주하는 이미지 공지 (모두에게 같은 카드)
*   카드: [페이지 이미지] · 구분선 · [1️⃣ 길드 규칙][2️⃣ 임시 음성 채널 안내] / [3️⃣ 명예/우수 혜택][4️⃣ 용병 안내]
*   설치할 때 4장을 한 번에 첨부(p1~p4.png) — 새 카드 형식은 카드가 가리키는 첨부만 화면에 보이므로,
*   버튼을 누르면 가리키는 이미지만 바꿈 (다시 올리지 않아 바로 넘어감). 재시작 후에도 버튼 동작.
*   이미지: notice/pages/1.png ~ 4.png (같은 이름으로 파일만 바꾸면 이미지 교체)
* + /공지사항설치 : (길마/서마) 지금 채널에 공지 카드 설치 (이전 카드는 삭제)
* date   2026.11.05
'''
import os

import discord
from discord import app_commands
from discord.ext import commands

from roleSetting.roleSetting import has_role
from config.guild_config import get_setting, set_setting

_DIR = os.path.join(os.path.dirname(__file__), 'pages')
_ACCENT = discord.Colour(0x3FB8F3)   # 길드 대표색
PAGES = [
    ('1️⃣', '길드 규칙'),
    ('2️⃣', '임시 음성 채널 안내'),
    ('3️⃣', '명예/우수 혜택'),
    ('4️⃣', '용병 안내'),
]
def _name(page):
    return f'p{page + 1}.png'   # 메시지에 붙는 첨부 이름 (페이지마다 따로)


def _all_files():
    return [discord.File(os.path.join(_DIR, f'{i + 1}.png'), filename=_name(i)) for i in range(len(PAGES))]


class _PageButton(discord.ui.Button):
    def __init__(self, page, current):
        emoji, label = PAGES[page]
        super().__init__(label=label, emoji=emoji, custom_id=f'nt_page:{page}',
                         style=discord.ButtonStyle.primary if page == current else discord.ButtonStyle.secondary)
        self.page = page

    async def callback(self, interaction: discord.Interaction):
        # 모두가 보는 카드 → 누르면 모두의 화면이 그 페이지로 바뀜. 첨부는 그대로 두고 카드만 교체(즉시).
        have = {a.filename for a in (interaction.message.attachments if interaction.message else [])}
        if all(_name(i) in have for i in range(len(PAGES))):
            await interaction.response.edit_message(view=NoticeCard(self.page))
            return
        # 예전 방식(사진 1장)으로 설치된 카드 → 이번 한 번만 4장을 올려 새 방식으로 바꿈
        await interaction.response.defer()
        try:
            await interaction.edit_original_response(view=NoticeCard(self.page), attachments=_all_files())
        except Exception as e:
            print(f'[notice] 페이지 전환 실패({self.page + 1}쪽): {e}')


class NoticeCard(discord.ui.LayoutView):
    def __init__(self, page=0):
        super().__init__(timeout=None)
        rows = []
        for start in range(0, len(PAGES), 2):   # 버튼 두 개씩 한 줄 (1·2 / 3·4)
            row = discord.ui.ActionRow()
            for i in range(start, min(start + 2, len(PAGES))):
                row.add_item(_PageButton(i, page))
            rows.append(row)
        self.add_item(discord.ui.Container(
            discord.ui.MediaGallery(discord.MediaGalleryItem(f'attachment://{_name(page)}',
                                                             description=PAGES[page][1])),
            discord.ui.Separator(),
            *rows,
            accent_colour=_ACCENT,
        ))


class Notice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(NoticeCard())   # 재시작 후에도 이미 올라간 카드 버튼 동작

    @app_commands.command(name='공지사항설치', description='(길마/서마) 이 채널에 공지사항 카드 설치')
    @app_commands.guild_only()
    async def install(self, interaction: discord.Interaction):
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        old_ch, old_msg = get_setting(interaction.guild.id, '_notice_ch'), get_setting(interaction.guild.id, '_notice_msg')
        if old_ch and old_msg:   # 이전 카드 정리 (다른 채널에 있어도)
            try:
                ch = interaction.guild.get_channel(int(old_ch)) or await self.bot.fetch_channel(int(old_ch))
                await (await ch.fetch_message(int(old_msg))).delete()
            except Exception:
                pass
        try:
            msg = await interaction.channel.send(view=NoticeCard(0), files=_all_files())   # 4장 한 번에
        except discord.Forbidden:
            await interaction.followup.send('이 채널에 메시지를 보낼 권한이 없어요. 봇 권한을 확인하세요.', ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f'설치 실패: {e}', ephemeral=True)
            return
        set_setting(interaction.guild.id, '_notice_ch', interaction.channel.id)
        set_setting(interaction.guild.id, '_notice_msg', msg.id)
        await interaction.followup.send('공지사항 카드를 설치했어요. 아래 버튼으로 페이지를 넘길 수 있어요.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(Notice(bot))
