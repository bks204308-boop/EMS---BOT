import os
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ---------- FORMULAIRE : DOSSIER PERSONNEL ----------
class DossierPersonnelModal(discord.ui.Modal, title="Dossier Personnel"):
    nom = discord.ui.TextInput(label="Nom complet", placeholder="Ex: Julien Moreau")
    age = discord.ui.TextInput(label="Âge", placeholder="Ex: 39")
    groupe_sanguin = discord.ui.TextInput(label="Groupe sanguin", placeholder="Ex: A+")
    allergies = discord.ui.TextInput(
        label="Allergies / Antécédents",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Pénicilline, asthme léger",
        required=False,
    )
    contact_urgence = discord.ui.TextInput(
        label="Contact d'urgence", placeholder="Ex: Sophie Moreau - 06 98 76 54 32", required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📋 Dossier Personnel", color=discord.Color.blue())
        embed.add_field(name="Nom", value=self.nom.value, inline=True)
        embed.add_field(name="Âge", value=self.age.value, inline=True)
        embed.add_field(name="Groupe sanguin", value=self.groupe_sanguin.value, inline=True)
        embed.add_field(
            name="Allergies / Antécédents",
            value=self.allergies.value or "Aucun",
            inline=False,
        )
        embed.add_field(
            name="Contact d'urgence",
            value=self.contact_urgence.value or "Non renseigné",
            inline=False,
        )
        embed.set_footer(text=f"Rempli par {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)


# ---------- FORMULAIRE : DOSSIER D'INTERVENTION ----------
class DossierInterventionModal(discord.ui.Modal, title="Dossier d'Intervention"):
    blessure = discord.ui.TextInput(
        label="Blessure", placeholder="Ex: Fracture ouverte jambe droite"
    )
    soins = discord.ui.TextInput(
        label="Soins effectués",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Immobilisation, désinfection, antalgique",
    )
    transport = discord.ui.TextInput(
        label="Transport", placeholder="Ex: CHU / Hôpital local", required=False
    )
    facture = discord.ui.TextInput(
        label="Montant facturé (€)", placeholder="Ex: 395", required=False
    )
    statut_facture = discord.ui.TextInput(
        label="Statut facturation", placeholder="Ex: Payé / En attente", required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🚑 Dossier d'Intervention", color=discord.Color.red())
        embed.add_field(name="Blessure", value=self.blessure.value, inline=False)
        embed.add_field(name="Soins effectués", value=self.soins.value, inline=False)
        embed.add_field(
            name="Transport", value=self.transport.value or "Non renseigné", inline=True
        )
        embed.add_field(
            name="Montant facturé",
            value=f"{self.facture.value} €" if self.facture.value else "N/A",
            inline=True,
        )
        embed.add_field(
            name="Statut",
            value=self.statut_facture.value or "Non renseigné",
            inline=True,
        )
        embed.set_footer(text=f"Rempli par {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)


# ---------- COMMANDES SLASH ----------
@bot.tree.command(name="dossier_personnel", description="Remplir un dossier personnel")
async def dossier_personnel(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierPersonnelModal())


@bot.tree.command(name="dossier_intervention", description="Remplir un dossier d'intervention")
async def dossier_intervention(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierInterventionModal())


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Connecté en tant que {bot.user}")


bot.run(TOKEN)
