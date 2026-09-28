"""
Serveur MCP minimal : un bloc-notes persistant ("NotesRapides").

Objectif pédagogique
--------------------
Ce fichier illustre les TROIS primitives du Model Context Protocol (MCP) :

  1. Ressource (resource) : une donnée en LECTURE SEULE que le client (Claude)
     peut consulter, identifiée par une URI (ici "notes://resume").
     -> C'est l'équivalent d'un "GET" : pas d'effet de bord.

  2. Outil (tool) : une ACTION que le LLM peut décider d'appeler lui-même,
     avec des arguments typés (ici ajouter / lire une note).
     -> C'est l'équivalent d'un "POST" / appel de fonction.

  3. Prompt : un MODÈLE DE MESSAGE pré-rédigé, que l'UTILISATEUR choisit
     explicitement (ex. via une commande slash dans Claude Code).
     -> Le serveur fabrique le texte, le LLM se contente de le recevoir.

Qui contrôle quoi ?
  - Ressources : contrôlées par l'application cliente.
  - Outils     : contrôlés par le modèle (le LLM décide de les appeler).
  - Prompts    : contrôlés par l'utilisateur.

Transport : "stdio". Le client lance ce script comme sous-processus et dialogue
avec lui en JSON-RPC via l'entrée / sortie standard. Conséquence importante :
il ne faut JAMAIS écrire sur stdout avec print(), sous peine de corrompre
le protocole (utiliser stderr ou le logging pour déboguer).

Lancement (cf. README) :
    claude mcp add notes-mcp -- uv run --directory $(pwd) server.py
"""

import json
from pathlib import Path

# MCPServer est la classe haut niveau du SDK Python officiel (anciennement
# "FastMCP"). Elle s'appuie sur des décorateurs pour exposer des fonctions
# Python comme primitives MCP.
from mcp.server.mcpserver import MCPServer

# Types du protocole utilisés pour construire les messages renvoyés par les prompts.
from mcp.types import PromptMessage, TextContent

# Création de l'instance du serveur. Le nom est celui annoncé au client
# lors de la phase d'initialisation (handshake MCP).
mcp = MCPServer("NotesRapides")

# Stockage volontairement simpliste : un fichier JSON à côté du script,
# de la forme {"titre": "contenu", ...}. Suffisant pour une démo.
NOTES_FILE = Path(__file__).parent / "notes.json"


# ---------------------------------------------------------------------
# Fonctions utilitaires (non exposées au client)
# ---------------------------------------------------------------------
# Le préfixe "_" est une convention : ces fonctions ne sont PAS décorées,
# elles restent donc invisibles pour le client MCP. Seules les fonctions
# décorées par @mcp.resource / @mcp.tool / @mcp.prompt sont publiées.

def _charger_notes() -> dict:
    """Lit le fichier JSON et renvoie le dictionnaire des notes."""
    if not NOTES_FILE.exists():
        return {}
    return json.loads(NOTES_FILE.read_text(encoding="utf-8"))


def _sauvegarder_notes(data: dict) -> None:
    """Écrit le dictionnaire des notes sur disque.

    ensure_ascii=False conserve les accents lisibles dans le fichier.
    """
    NOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# =====================================================================
# 1. RESSOURCE
# =====================================================================
# @mcp.resource associe une URI à une fonction. Quand le client demande
# à lire "notes://resume", le SDK appelle obtenir_resume() et renvoie
# la chaîne produite. Le schéma "notes://" est libre : on l'invente.
# Dans Claude Code, on peut référencer la ressource avec "@notes-mcp:...".
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
# @mcp.tool() publie la fonction comme un outil appelable par le LLM.
# Le SDK déduit automatiquement :
#   - le NOM de l'outil       -> nom de la fonction ("ajouter_note")
#   - la DESCRIPTION           -> la docstring (c'est ce que lit le LLM
#                                 pour décider QUAND utiliser l'outil :
#                                 elle doit donc être claire et précise !)
#   - le SCHÉMA des arguments  -> les annotations de type (titre: str, ...)
#                                 converties en JSON Schema.
@mcp.tool()
def ajouter_note(titre: str, contenu: str) -> str:
    """Enregistre une note dans le bloc-notes."""
    # Validation des entrées : on renvoie un message d'erreur lisible plutôt
    # que de lever une exception. Le LLM lira ce texte et pourra se corriger.
    if not titre.strip() or not contenu.strip():
        return "Erreur : le titre et le contenu ne peuvent pas être vides."
    notes = _charger_notes()
    notes[titre.strip()] = contenu.strip()  # un titre existant est écrasé
    _sauvegarder_notes(notes)
    # La valeur de retour est renvoyée au LLM comme résultat de l'outil.
    return f"Note '{titre}' enregistrée avec succès."


@mcp.tool()
def lire_note(titre: str) -> str:
    """Lit le contenu d'une note spécifique à partir de son titre exact."""
    notes = _charger_notes()
    if titre not in notes:
        # Astuce : en cas d'échec, on liste les titres disponibles. Le LLM
        # dispose ainsi de l'information pour réessayer avec le bon titre.
        disponibles = ", ".join(notes.keys()) if notes else "aucune"
        return f"Note introuvable. Titres existants : {disponibles}."
    return notes[titre]


# =====================================================================
# 3. PROMPTS (La 3e primitive MCP)
# =====================================================================
# @mcp.prompt() publie un modèle de conversation. Contrairement aux outils,
# le LLM ne les appelle pas de lui-même : c'est l'utilisateur qui les
# déclenche (dans Claude Code : /mcp__notes-mcp__reformuler_note).
# La fonction renvoie une liste de messages qui seront injectés dans la
# conversation, comme si l'utilisateur les avait tapés.

# Exemple 1 : Prompt avec argument
# L'argument "titre" sera demandé à l'utilisateur au moment de l'invocation.
@mcp.prompt()
def reformuler_note(titre: str) -> list[PromptMessage]:
    """Prépare un prompt demandant au LLM de restructurer et corriger une note précise."""
    notes = _charger_notes()
    contenu = notes.get(titre, "[Note inexistante]")

    # On construit une consigne complète : rôle, données (délimitées
    # clairement par des balises DEBUT / FIN) et tâche attendue.
    consigne = (
        f"Tu es un assistant éditorial.\n"
        f"Voici le contenu brut de la note intitulée « {titre} » :\n\n"
        f"--- DEBUT NOTE ---\n{contenu}\n--- FIN NOTE ---\n\n"
        f"Consigne : Reformule cette note sous forme de points d'action clairs, "
        f"corrige la syntaxe et propose 3 mots-clés de classement."
    )

    # Un seul message de rôle "user" contenant du texte.
    # (Un prompt pourrait aussi renvoyer plusieurs messages, alternant
    # "user" et "assistant", pour amorcer un dialogue.)
    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=consigne),
        )
    ]


# Exemple 2 : Prompt sans argument (synthèse globale)
# Ici le serveur injecte TOUTES les notes dans le prompt : c'est une manière
# de fournir du contexte au LLM sans qu'il ait à appeler d'outil.
@mcp.prompt()
def synthese_hebdo() -> list[PromptMessage]:
    """Génère un canevas pour demander une synthèse globale de toutes les notes."""
    notes = _charger_notes()
    # Chaque note devient une section Markdown "### titre\ncontenu".
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


# Point d'entrée : démarre la boucle du serveur, qui attend les requêtes
# JSON-RPC du client sur stdin et répond sur stdout.
if __name__ == "__main__":
    mcp.run(transport="stdio")
