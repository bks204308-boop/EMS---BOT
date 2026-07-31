import os
import io
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

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


# ---------- DONNÉES DE TRIAGE (fictif, RP) ----------
TRIAGE_DATA = {
    "tete": {
        "label": "Tête",
        "cases": [
            {
                "title": "Traumatisme crânien",
                "symptoms": "Choc à la tête, maux de tête intenses, vertiges, confusion",
                "soins": "Immobilisation du patient, surveillance neurologique rapprochée, TDM crânien pour écarter une hémorragie.",
                "meds": "Antalgique léger (paracétamol), anti-nauséeux si vomissements.",
                "urgent": True,
            },
            {
                "title": "Plaie du cuir chevelu",
                "symptoms": "Saignement abondant, plaie ouverte",
                "soins": "Nettoyage de la plaie, points de suture si nécessaire, pansement compressif.",
                "meds": "Antiseptique local, antalgique simple.",
            },
            {
                "title": "Céphalée sévère / migraine",
                "symptoms": "Douleur pulsatile, sensibilité à la lumière",
                "soins": "Repos en environnement calme et sombre, surveillance de l'évolution.",
                "meds": "Antalgique, anti-inflammatoire, antiémétique si nausées.",
            },
            {
                "title": "Perte de connaissance brève",
                "symptoms": "Évanouissement, pâleur, retour à la conscience rapide",
                "soins": "Position latérale de sécurité, prise des constantes (tension, pouls, glycémie).",
                "meds": "Selon la cause identifiée — à réévaluer après bilan.",
            },
        ],
    },
    "cou": {
        "label": "Cou",
        "cases": [
            {
                "title": "Entorse cervicale / torticolis",
                "symptoms": "Douleur, raideur, mobilité réduite",
                "soins": "Pose d'un collier cervical souple, repos.",
                "meds": "Antalgique, décontractant musculaire.",
            },
            {
                "title": "Traumatisme cervical (accident)",
                "symptoms": "Douleur vive, engourdissement dans les bras",
                "soins": "Immobilisation stricte (collier rigide + plan dur), imagerie avant toute mobilisation.",
                "meds": "Antalgique fort sous surveillance médicale.",
                "urgent": True,
            },
            {
                "title": "Gêne respiratoire / gonflement",
                "symptoms": "Œdème visible, voix rauque, difficulté à respirer",
                "soins": "Surveillance des voies aériennes en priorité, oxygène si besoin.",
                "meds": "Corticoïde, antihistaminique si origine allergique suspectée.",
                "urgent": True,
            },
        ],
    },
    "thorax": {
        "label": "Thorax",
        "cases": [
            {
                "title": "Douleur thoracique (suspicion cardiaque)",
                "symptoms": "Oppression, douleur irradiant dans le bras ou la mâchoire",
                "soins": "ECG immédiat, monitoring cardiaque continu, oxygène.",
                "meds": "Aspirine, dérivé nitré, antalgique.",
                "urgent": True,
            },
            {
                "title": "Fracture de côte",
                "symptoms": "Douleur à l'inspiration, point douloureux localisé",
                "soins": "Contention légère, kinésithérapie respiratoire pour éviter les complications.",
                "meds": "Antalgique, anti-inflammatoire.",
            },
            {
                "title": "Crise d'asthme / gêne respiratoire",
                "symptoms": "Sifflement, essoufflement, toux",
                "soins": "Position assise, oxygène, nébulisation.",
                "meds": "Bronchodilatateur, corticoïde inhalé.",
            },
        ],
    },
    "abdomen": {
        "label": "Abdomen",
        "cases": [
            {
                "title": "Douleur abdominale aiguë",
                "symptoms": "Douleur localisée (souvent en bas à droite), fièvre",
                "soins": "Échographie ou scanner abdominal, surveillance, jeûne en prévision d'une éventuelle opération.",
                "meds": "Antalgique, antibiotique si infection confirmée.",
                "urgent": True,
            },
            {
                "title": "Plaie pénétrante abdominale",
                "symptoms": "Plaie ouverte, saignement, signes de choc possibles",
                "soins": "Compression de la plaie, pose de perfusion, transfert rapide au bloc opératoire.",
                "meds": "Antibiotique à large spectre, antalgique fort.",
                "urgent": True,
            },
            {
                "title": "Gastro-entérite",
                "symptoms": "Vomissements, diarrhée, signes de déshydratation",
                "soins": "Réhydratation (orale ou par perfusion), repos digestif.",
                "meds": "Anti-nauséeux, solution de réhydratation orale.",
            },
        ],
    },
    "bras": {
        "label": "Bras",
        "cases": [
            {
                "title": "Fracture du bras / poignet",
                "symptoms": "Douleur, déformation visible, impossibilité de bouger",
                "soins": "Immobilisation par attelle ou plâtre, radiographie de contrôle.",
                "meds": "Antalgique, anti-inflammatoire.",
            },
            {
                "title": "Coupure / plaie superficielle",
                "symptoms": "Saignement modéré, plaie propre ou souillée",
                "soins": "Nettoyage, suture si la plaie est profonde, pansement.",
                "meds": "Antiseptique local, antalgique léger.",
            },
            {
                "title": "Brûlure",
                "symptoms": "Rougeur, cloques, douleur au contact",
                "soins": "Refroidissement immédiat à l'eau tempérée, pansement stérile non adhérent.",
                "meds": "Crème cicatrisante, antalgique.",
            },
        ],
    },
    "jambes": {
        "label": "Jambes",
        "cases": [
            {
                "title": "Entorse de la cheville",
                "symptoms": "Gonflement, douleur, difficulté à marcher",
                "soins": "Protocole repos / glace / compression / élévation, immobilisation légère.",
                "meds": "Anti-inflammatoire, antalgique.",
            },
            {
                "title": "Fracture de jambe",
                "symptoms": "Douleur intense, déformation visible",
                "soins": "Immobilisation, radiographie, chirurgie parfois nécessaire.",
                "meds": "Antalgique fort, anticoagulant préventif.",
                "urgent": True,
            },
            {
                "title": "Suspicion de phlébite",
                "symptoms": "Jambe gonflée, chaude et douloureuse",
                "soins": "Échographie doppler de contrôle, surveillance rapprochée.",
                "meds": "Anticoagulant.",
                "urgent": True,
            },
        ],
    },
    "vitaux": {
        "label": "Signes Vitaux",
        "cases": [
            {
                "title": "Tachycardie",
                "symptoms": "Pouls rapide (>100 bpm), palpitations, parfois vertiges",
                "soins": "Mise au repos, monitoring cardiaque continu, ECG, recherche de la cause (fièvre, stress, hémorragie...).",
                "meds": "Bêta-bloquant si indiqué, selon la cause identifiée.",
                "urgent": True,
            },
            {
                "title": "Bradycardie",
                "symptoms": "Pouls lent (<60 bpm), fatigue, sensation de malaise",
                "soins": "Monitoring cardiaque, ECG, surveillance de la tension artérielle.",
                "meds": "Atropine si symptomatique et sous surveillance médicale.",
                "urgent": True,
            },
            {
                "title": "Pouls faible / filant",
                "symptoms": "Pouls difficile à percevoir, peau pâle et moite, faiblesse",
                "soins": "Position allongée jambes surélevées, oxygène, recherche d'une cause (choc, hémorragie), perfusion si besoin.",
                "meds": "Remplissage vasculaire (perfusion), selon protocole de choc.",
                "urgent": True,
            },
            {
                "title": "Hypotension artérielle",
                "symptoms": "Vertiges, vision trouble, faiblesse, pâleur",
                "soins": "Position allongée jambes surélevées, surveillance de la tension, hydratation.",
                "meds": "Perfusion si sévère, selon cause identifiée.",
            },
            {
                "title": "Hypertension artérielle sévère",
                "symptoms": "Maux de tête intenses, bourdonnements, vision floue",
                "soins": "Repos au calme, surveillance rapprochée de la tension, ECG de contrôle.",
                "meds": "Antihypertenseur sous surveillance médicale.",
                "urgent": True,
            },
            {
                "title": "Détresse respiratoire / hypoxie",
                "symptoms": "Essoufflement marqué, lèvres bleutées, confusion",
                "soins": "Position assise, oxygène à haut débit, surveillance de la saturation en continu.",
                "meds": "Bronchodilatateur si origine respiratoire, oxygénothérapie.",
                "urgent": True,
            },
        ],
    },
}


# ---------- GÉNÉRATION DE L'IMAGE DU CORPS (zone en surbrillance) ----------
ZONE_SHAPES = {
    "tete": [("ellipse", 100, 42, 30)],
    "cou": [("rect", 86, 70, 28, 20)],
    "thorax": [("rect", 62, 94, 76, 80)],
    "abdomen": [("rect", 68, 178, 64, 60)],
    "bras": [("rect", 26, 98, 30, 130), ("rect", 144, 98, 30, 130)],
    "jambes": [("rect", 70, 242, 26, 150), ("rect", 104, 242, 26, 150)],
    "vitaux": [("rect", 62, 94, 76, 80)],
}

BASE_FILL = (47, 143, 209, 45)
BASE_STROKE = (47, 143, 209, 255)
HL_FILL = (92, 179, 238, 255)
HL_STROKE = (255, 255, 255, 255)
BG_COLOR = (16, 30, 51, 255)


def generate_body_image(highlight: str = None) -> io.BytesIO:
    scale = 3
    w, h = 200 * scale, 420 * scale
    img = Image.new("RGBA", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    for zone_key, shapes in ZONE_SHAPES.items():
        is_hl = zone_key == highlight
        fill = HL_FILL if is_hl else BASE_FILL
        stroke = HL_STROKE if is_hl else BASE_STROKE
        width = 6 if is_hl else 3
        for shape in shapes:
            kind = shape[0]
            if kind == "ellipse":
                _, cx, cy, r = shape
                cx, cy, r = cx * scale, cy * scale, r * scale
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=stroke, width=width)
            else:
                _, x, y, ww, hh = shape
                x, y, ww, hh = x * scale, y * scale, ww * scale, hh * scale
                draw.rounded_rectangle(
                    [x, y, x + ww, y + hh], radius=10 * scale, fill=fill, outline=stroke, width=width
                )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def build_case_embed(zone_key: str, case: dict) -> discord.Embed:
    title = case["title"]
    if case.get("urgent"):
        title += " ⚠️ Priorité 1"
    embed = discord.Embed(
        title=title,
        description=f"Zone : **{TRIAGE_DATA[zone_key]['label']}**",
        color=discord.Color.red() if case.get("urgent") else discord.Color.teal(),
    )
    embed.add_field(name="Symptômes", value=case["symptoms"], inline=False)
    embed.add_field(name="Soins", value=case["soins"], inline=False)
    embed.add_field(name="Médicaments", value=case["meds"], inline=False)
    embed.set_footer(text="Contenu fictif pour RP — pas un guide médical réel")
    return embed


class CaseSelect(discord.ui.Select):
    def __init__(self, zone_key: str):
        self.zone_key = zone_key
        options = [
            discord.SelectOption(
                label=c["title"][:100],
                description=c["symptoms"][:100],
                value=str(i),
                emoji="⚠️" if c.get("urgent") else None,
            )
            for i, c in enumerate(TRIAGE_DATA[zone_key]["cases"])
        ]
        super().__init__(placeholder="Choisis un cas...", options=options)

    async def callback(self, interaction: discord.Interaction):
        case = TRIAGE_DATA[self.zone_key]["cases"][int(self.values[0])]
        embed = build_case_embed(self.zone_key, case)
        await interaction.response.edit_message(embed=embed, view=self.view)


class CaseView(discord.ui.View):
    def __init__(self, zone_key: str):
        super().__init__(timeout=120)
        self.add_item(CaseSelect(zone_key))
        self.add_item(BackToZoneButton())


class BackToZoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⬅ Changer de zone", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🩺 Fiche de Triage",
            description="Choisis une zone du corps pour voir les cas possibles, les soins et les médicaments associés.",
            color=discord.Color.blue(),
        )
        file = discord.File(generate_body_image(), filename="body.png")
        embed.set_image(url="attachment://body.png")
        await interaction.response.edit_message(embed=embed, view=ZoneView(), attachments=[file])


class ZoneSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=data["label"], value=key)
            for key, data in TRIAGE_DATA.items()
        ]
        super().__init__(placeholder="Choisis une zone du corps...", options=options)

    async def callback(self, interaction: discord.Interaction):
        zone_key = self.values[0]
        embed = discord.Embed(
            title=f"🩺 Triage — {TRIAGE_DATA[zone_key]['label']}",
            description="Choisis un cas dans la liste ci-dessous.",
            color=discord.Color.blue(),
        )
        file = discord.File(generate_body_image(zone_key), filename="body.png")
        embed.set_image(url="attachment://body.png")
        await interaction.response.edit_message(embed=embed, view=CaseView(zone_key), attachments=[file])


class ZoneView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ZoneSelect())


@bot.tree.command(name="triage", description="Outil de triage RP : zone du corps -> cas -> soins")
async def triage(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🩺 Fiche de Triage",
        description="Choisis une zone du corps pour voir les cas possibles, les soins et les médicaments associés.",
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Contenu fictif pour RP — pas un guide médical réel")
    file = discord.File(generate_body_image(), filename="body.png")
    embed.set_image(url="attachment://body.png")
    await interaction.response.send_message(embed=embed, view=ZoneView(), file=file, ephemeral=True)


GUILD_ID = discord.Object(id=1527797628228735047)


@bot.event
async def on_guild_join(guild: discord.Guild):
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"Commandes synchronisées sur le nouveau serveur : {guild.name}")


@bot.event
async def on_ready():
    bot.tree.copy_global_to(guild=GUILD_ID)
    await bot.tree.sync(guild=GUILD_ID)
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync(guild=None)
    print(f"Connecté en tant que {bot.user}")


bot.run(TOKEN)
