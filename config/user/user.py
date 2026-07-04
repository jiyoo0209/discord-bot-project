'''
* 길드원 추가/삭제
* author LJY
* date   2026.06.30
'''
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from sheet.sheet import add_user, remove_user
from roleSetting.roleSetting import has_role, user_autocomplete
from config.guild_config import get_role_id


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
                    view=_result_card('⏱ 시간 초과 — `/탈퇴` 를 다시 실행하세요.', discord.Colour.dark_grey()))
            except Exception:
                pass

@app_commands.guild_only()
class UserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='가입', description='새로운 길드원 추가')
    @app_commands.describe(user_name='테런 닉네임', discord_user='대상 디스코드 유저')
    async def register_user(self, interaction: discord.Interaction, user_name: str, discord_user: discord.Member):
        # 권한 체크는 defer 전에 (즉시 응답 가능하고, 거부 메시지도 여기서 끝냄)
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)  # 처리 중 표시 (이후엔 followup 만 사용)
        try:
            # 시트 쓰기는 블로킹이라 스레드로 분리 (discord_id 저장 → 자동 본인 인식용)
            success, message = await asyncio.to_thread(add_user, user_name, discord_user.id)

            # 시트 등록에 실패(이미 등록 등)하면 역할 변경 없이 종료
            if not success:
                await interaction.followup.send(message, ephemeral=True)
                return

            # 성공했을 때만: 입장대기 제거 → 일반 역할 부여 (서버별 매핑, 미설정/삭제 안전 처리)
            wait_id = get_role_id(interaction.guild.id, '입장대기')
            normal_id = get_role_id(interaction.guild.id, '일반')
            wait_role = interaction.guild.get_role(wait_id) if wait_id else None
            normal_role = interaction.guild.get_role(normal_id) if normal_id else None
            # 시트는 이미 커밋됨 → 역할 실패(권한/역할위치)를 별도로 잡아 정확히 안내
            try:
                if wait_role:
                    await discord_user.remove_roles(wait_role)
                if normal_role:
                    await discord_user.add_roles(normal_role)
            except discord.Forbidden:
                await interaction.followup.send(
                    message + '\n⚠️ 시트 등록은 됐지만 역할 부여 실패 — 봇 역할을 일반/입장대기보다 '
                              '위로 올리고 역할 관리 권한을 확인하세요.', ephemeral=True)
                return

            note = '' if (wait_role and normal_role) else '\n⚠️ 역할 매핑이 없어 시트만 반영됨 (`/역할설정` 필요)'
            await interaction.followup.send(message + note, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f'오류: {e}', ephemeral=True)

    @app_commands.command(name='탈퇴', description='길드원 탈퇴 (데이터 삭제)')
    @app_commands.describe(user_name='탈퇴할 테런 닉네임')
    @app_commands.autocomplete(user_name=user_autocomplete)
    async def unregister_user(self, interaction: discord.Interaction, user_name: str):
        # 길마·서마 (버튼 탈퇴와 권한 통일)
        if not (has_role(interaction, '길마') or has_role(interaction, '서마')):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return
        # 하드삭제라 되돌릴 수 없음 → V2 확인 카드 (embed/content 없이 view 만)
        view = ConfirmDeleteView(user_name)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.origin = interaction  # on_timeout 에서 이 카드를 갱신할 수 있게

async def setup(bot):
    await bot.add_cog(UserCog(bot))