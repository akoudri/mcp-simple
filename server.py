import json
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from mcp.types import PromptMessage, TextContent

mcp = MCPServer("NotesRapides")

NOTES_FILE = Path(__file__).parent / "notes.json"


def _charger_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    return json.loads(NOTES_FILE.read_text(encoding="utf-8"))


def _sauvegarder_notes(data: dict) -> None:
    NOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# =====================================================================
# 1. RESSOURCE
# =====================================================================
@mcp.resource("notes://resume")
def obtenir_resume() -> str:
    """Fournit un résumé textuel des notes existantes et leur nombre."""
    notes = _charger_notes()
    if not notes:
        return "Aucune note enregistrée."
    lignes = [f"- {titre} : {len(contenu)} caractères" for titre, contenu in notes.items()]
    return f"Total : {len(notes)} note(s)\n" + "\n".join(lignes)


# =====================================================================
# 2. OUTILS
# =====================================================================
@mcp.tool()
def ajouter_note(titre: str, contenu: str) -> str:
    """Enregistre une note dans le bloc-notes."""
    if not titre.strip() or not contenu.strip():
        return "Erreur : le titre et le contenu ne peuvent pas être vides."
    notes = _charger_notes()
    notes[titre.strip()] = contenu.strip()
    _sauvegarder_notes(notes)
    return f"Note '{titre}' enregistrée avec succès."


@mcp.tool()
def lire_note(titre: str) -> str:
    """Lit le contenu d'une note spécifique à partir de son titre exact."""
    notes = _charger_notes()
    if titre not in notes:
        disponibles = ", ".join(notes.keys()) if notes else "aucune"
        return f"Note introuvable. Titres existants : {disponibles}."
    return notes[titre]


# =====================================================================
# 3. PROMPTS (La 3e primitive MCP)
# =====================================================================

# Exemple 1 : Prompt avec argument
@mcp.prompt()
def reformuler_note(titre: str) -> list[PromptMessage]:
    """Prépare un prompt demandant au LLM de restructurer et corriger une note précise."""
    notes = _charger_notes()
    contenu = notes.get(titre, "[Note inexistante]")

    consigne = (
        f"Tu es un assistant éditorial.\n"
        f"Voici le contenu brut de la note intitulée « {titre} » :\n\n"
        f"--- DEBUT NOTE ---\n{contenu}\n--- FIN NOTE ---\n\n"
        f"Consigne : Reformule cette note sous forme de points d'action clairs, "
        f"corrige la syntaxe et propose 3 mots-clés de classement."
    )

    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=consigne),
        )
    ]


# Exemple 2 : Prompt sans argument (synthèse globale)
@mcp.prompt()
def synthese_hebdo() -> list[PromptMessage]:
    """Génère un canevas pour demander une synthèse globale de toutes les notes."""
    notes = _charger_notes()
    corps_notes = "\n\n".join([f"### {t}\n{c}" for t, c in notes.items()]) or "Aucune note."

    consigne = (
        f"Voici l'ensemble des notes accumulées cette semaine :\n\n{corps_notes}\n\n"
        f"Rédige un compte-rendu synthétique en mettant en avant :\n"
        f"1. Les décisions prises\n"
        f"2. Les points de vigilance\n"
        f"3. Les prochaines étapes."
    )

    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=consigne),
        )
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")