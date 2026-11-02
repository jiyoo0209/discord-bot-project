'''
* 탈퇴 확인 카드 (길드원 목록 [탈퇴] · 관리 패널 탈퇴에서 사용) — /가입·/탈퇴 명령은 제거됨
* author LJY
* date   2026.06.30
'''
import asyncio
import discord
from sheet.sheet import remove_user


# /탈퇴 확인 다이얼로그 — Components V2 카드 (임시 View, ephemeral). 버튼: [탈퇴]/[취소]
def _result_card(text, colour):
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.Container(discord.ui.TextDisplay(text), accent_colour=colour))
    return v


class _ConfirmButtons(discord.ui.ActionRow):
    def __init__(self, owner):
        super().__init__()
        self._owner = owner  # 부모 ConfirmDeleteView (user_name/stop 접근). 'parent'는 예약됨

    @discord.ui.button(label='탈퇴', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        ok, message = await asyncio.to_thread(remove_user, self._owner.user_name)
        self._owner.stop()
        await interaction.edit_original_response(
            view=_result_card(message, discord.Colour.green() if ok else discord.Colour.red()))

    @discord.ui.button(label='취소', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._owner.stop()
        await interaction.response.edit_message(
            view=_result_card('취소되었습니다.', discord.Colour.dark_grey()))


class ConfirmDeleteView(discord.ui.LayoutView):
    def __init__(self, user_name):
        super().__init__(timeout=60)
        self.user_name = user_name
        self.origin = None  # 원본 상호작용 — 타임아웃 시 카드 갱신용
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f'## ⚠️ 탈퇴 확인\n**{user_name}** 님을 탈퇴 처리할까요?\n'
                '-# user·포인트·사유가 모두 삭제되고 되돌릴 수 없습니다.'),
            discord.ui.Separator(),
            _ConfirmButtons(self),
            accent_colour=discord.Colour.red(),
        ))

    async def on_timeout(self):
        # 만료 후 버튼 클릭 시 '상호작용 실패'만 뜨는 혼란 방지 → 안내 카드로 교체
        if self.origin is not None:
            try:
                await self.origin.edit_original_response(
                    view=_result_card('⏱ 시간 초과 — 탈퇴 버튼을 다시 눌러주세요.', discord.Colour.dark_grey()))
            except Exception:
                pass
