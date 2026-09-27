import json
from pathlib import Path
from mcp.server.mcpserver import MCPServer

# En 2.x, FastMCP devient MCPServer
mcp = MCPServer("NotesRapides")

NOTES_FILE = Path(__file__).parent / "notes.json"


def _charger_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    return json.loads(NOTES_FILE.read_text(encoding="utf-8"))


def _sauvegarder_notes(data: dict) -> None:
    NOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --- RESSOURCE ---
@mcp.resource("notes://resume")
def obtenir_resume() -> str:
    """Fournit un résumé textuel des notes existantes et leur nombre."""
    notes = _charger_notes()
    if not notes:
        return "Aucune note enregistrée."
    lignes = [f"- {titre} : {len(contenu)} caractères" for titre, contenu in notes.items()]
    return f"Total : {len(notes)} note(s)\n" + "\n".join(lignes)


# --- OUTIL 1 : Écriture ---
@mcp.tool()
def ajouter_note(titre: str, contenu: str) -> str:
    """Enregistre une note dans le bloc-notes.

    À appeler dès que l'utilisateur demande de noter ou mémoriser une information.
    """
    if not titre.strip() or not contenu.strip():
        return "Erreur : le titre et le contenu ne peuvent pas être vides."

    notes = _charger_notes()
    notes[titre.strip()] = contenu.strip()
    _sauvegarder_notes(notes)
    return f"Note '{titre}' enregistrée avec succès."


# --- OUTIL 2 : Lecture ---
@mcp.tool()
def lire_note(titre: str) -> str:
    """Lit le contenu d'une note spécifique à partir de son titre exact."""
    notes = _charger_notes()
    if titre not in notes:
        disponibles = ", ".join(notes.keys()) if notes else "aucune"
        return f"Note introuvable. Titres existants : {disponibles}."

    return notes[titre]


if __name__ == "__main__":
    mcp.run(transport="stdio")