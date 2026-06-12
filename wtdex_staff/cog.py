import csv
import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from bd_models.models import Ball, BallInstance, Player
from ballsdex.core.image_generator.image_gen import draw_card

STAFF_IDS = [712232017311563847, 668041551389392896, 784527909414502411, 1141857479047778394]


class Staff(commands.GroupCog, group_name="staff"):
    """Staff-only server management commands."""

    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in STAFF_IDS:
            await interaction.response.send_message("Unauthorized.", ephemeral=True)
            return False
        return True

    async def plane_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        current = (current or "").strip()

        qs = Ball.objects.all().order_by("country")
        if current:
            qs = qs.filter(country__icontains=current)

        choices: list[app_commands.Choice[str]] = []
        async for ball in qs[:25]:
            choices.append(app_commands.Choice(name=ball.country, value=str(ball.pk)))
        return choices

    @app_commands.command(name="leave", description="Force War Thunder Dex to leave a server")
    @app_commands.describe(guild_id="The ID of the server to leave")
    async def leave_guild(self, interaction: discord.Interaction, guild_id: str):
        try:
            clean_id = int(guild_id.strip())
        except ValueError:
            return await interaction.response.send_message("Invalid ID.", ephemeral=True)

        guild_obj = self.bot.get_guild(clean_id)

        if not guild_obj:
            return await interaction.response.send_message(
                f"Server `{clean_id}` not found in cache.",
                ephemeral=True,
            )

        name = guild_obj.name

        await interaction.response.send_message(
            f"Leaving **{name}** (`{clean_id}`)...",
            ephemeral=True,
        )

        try:
            await guild_obj.leave()
        except Exception as e:
            try:
                await interaction.followup.send(f"Error while leaving: {e}", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="name", description="Show the catch names of a plane")
    @app_commands.describe(plane="The plane to inspect")
    @app_commands.autocomplete(plane=plane_autocomplete)
    async def plane_name(self, interaction: discord.Interaction, plane: str):
        await interaction.response.defer(ephemeral=True)

        try:
            plane_id = int(plane)
        except ValueError:
            return await interaction.followup.send("Invalid plane selection.", ephemeral=True)

        ball = await Ball.objects.filter(pk=plane_id).afirst()
        if not ball:
            return await interaction.followup.send("Plane not found.", ephemeral=True)

        raw_catch_names = ball.catch_names
        catch_names_list: list[str] = []

        if raw_catch_names:
            if isinstance(raw_catch_names, list):
                catch_names_list = [str(x).strip() for x in raw_catch_names if str(x).strip()]
            elif isinstance(raw_catch_names, str):
                parsed = None

                try:
                    parsed = json.loads(raw_catch_names)
                except Exception:
                    parsed = None

                if isinstance(parsed, list):
                    catch_names_list = [str(x).strip() for x in parsed if str(x).strip()]
                else:
                    catch_names_list = [
                        x.strip()
                        for x in raw_catch_names.replace("\r", "\n").replace(",", "\n").split("\n")
                        if x.strip()
                    ]
            else:
                catch_names_list = [str(raw_catch_names).strip()]

        if catch_names_list:
            catch_names_text = "\n".join(f"- `{name}`" for name in catch_names_list)
        else:
            catch_names_text = "None set."

        await interaction.followup.send(
            f"**Plane:** {ball.country}\n"
            f"**Catch names:**\n{catch_names_text}",
            ephemeral=True,
        )

    @app_commands.command(name="inspect", description="Inspect a plane (spawn image,card & base stats)")
    @app_commands.describe(plane="The plane to inspect")
    @app_commands.autocomplete(plane=plane_autocomplete)
    async def inspect_plane(self, interaction: discord.Interaction, plane: str):
        await interaction.response.defer(ephemeral=True)

        try:
            plane_id = int(plane)
        except ValueError:
            return await interaction.followup.send("Invalid plane selection.", ephemeral=True)

        ball = await Ball.objects.filter(pk=plane_id).afirst()
        if not ball:
            return await interaction.followup.send("Plane not found.", ephemeral=True)

        files: list[discord.File] = []

        if getattr(ball, "wild_card", None):
            try:
                files.append(discord.File(ball.wild_card.path, filename="spawn_image.png"))
            except Exception:
                pass

        try:
            preview_instance = BallInstance(
                ball=ball,
                health_bonus=0,
                attack_bonus=0,
                special=None,
            )
            rendered_image, save_kwargs = draw_card(preview_instance)
            rendered_buffer = io.BytesIO()
            rendered_format = str(save_kwargs.get("format", "WEBP")).upper()
            rendered_image.save(rendered_buffer, format=rendered_format)
            rendered_buffer.seek(0)
            ext = rendered_format.lower()
            files.append(discord.File(rendered_buffer, filename=f"rendered_card.{ext}"))
        except Exception:
            pass

        stats_text = (
            f"**Plane:** {ball.country}\n"
            f"**Base Stats:**\n"
            f"HP: `{ball.health}`\n"
            f"ATK: `{ball.attack}`"
        )

        if files:
            await interaction.followup.send(content=stats_text, files=files, ephemeral=True)
        else:
            await interaction.followup.send(f"{stats_text}\n\n(No images available.)", ephemeral=True)

    @app_commands.command(name="restore", description="Restore Planes from CSV to a user")
    async def restore_inventory(self, interaction: discord.Interaction, user_id: str, csv_file: discord.Attachment):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            discord_id = int(user_id)
        except:
            return await interaction.followup.send("Invalid user id", ephemeral=True)

        data = (await csv_file.read()).decode("utf-8-sig")

        player, _ = await Player.objects.aget_or_create(discord_id=discord_id)

        special_model = BallInstance._meta.get_field("special").remote_field.model

        restored = 0
        failed = 0

        reader = csv.DictReader(io.StringIO(data))

        for row in reader:
            plane_name = (row.get("plane") or "").strip()
            special_name = (row.get("special_card") or "").strip()

            if not plane_name:
                failed += 1
                continue

            ball = await Ball.objects.filter(country__iexact=plane_name).afirst()
            if not ball:
                failed += 1
                continue

            special_id = None
            if special_name and special_name.lower() not in ["nan", "none", ""]:
                special = await special_model.objects.filter(name__iexact=special_name).afirst()
                if special:
                    special_id = special.pk

            await BallInstance.objects.acreate(
                player=player,
                ball=ball,
                special_id=special_id,
                attack_bonus=0,
                health_bonus=0,
                tradeable=True,
                deleted=False,
            )

            restored += 1

        await interaction.followup.send(
            f"✅ Restored {restored}\n❌ Failed {failed}",
            ephemeral=True
        )

    @app_commands.command(name="export", description="Export a user's inventory to CSV")
    async def export_inventory(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            discord_id = int(user_id.strip())
        except:
            return await interaction.followup.send("Invalid user id", ephemeral=True)

        player = await Player.objects.filter(discord_id=discord_id).afirst()
        if not player:
            return await interaction.followup.send("User has no data.", ephemeral=True)

        instances = BallInstance.objects.filter(player=player, tradeable=True).select_related("ball", "special")

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["plane", "special_card"])

        count = 0

        async for inst in instances:
            plane_name = inst.ball.country if inst.ball else "Unknown"
            special_name = inst.special.name if getattr(inst, "special", None) else ""

            writer.writerow([plane_name, special_name])
            count += 1

        output.seek(0)

        file = discord.File(
            io.BytesIO(output.getvalue().encode()),
            filename=f"export_{discord_id}.csv"
        )

        await interaction.followup.send(
            content=f"Exported **{count}** planes for <@{discord_id}>",
            file=file,
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Staff(bot))
