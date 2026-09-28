"""
Serveur MCP "pont" vers Blender ("BlenderBridge").

Objectif pédagogique
--------------------
Montrer qu'un serveur MCP n'a pas besoin de TOUT faire lui-même : il peut
servir d'ADAPTATEUR entre un LLM et une application existante.

Architecture (deux processus distincts) :

    Claude Code  --(MCP / stdio)-->  blender_mcp_server.py  --(TCP 9876)-->  Blender
     (client)                         (ce fichier)                           (blender_server.py)

  - Ce fichier est lancé par Claude Code (cf. README) et parle MCP.
  - Il ne peut PAS importer `bpy` : ce module n'existe qu'à l'intérieur de
    Blender. Il envoie donc du code Python, sous forme de texte, via une
    socket TCP locale.
  - Le script blender_server.py, exécuté DANS Blender, reçoit ce code et
    l'exécute avec accès à `bpy`.

Protocole maison (très simple) entre les deux :
  - requête  : {"code": "<script python>"}, suivie de la fermeture du côté
               écriture de la socket (fin de la requête)
  - réponse  : {"status": "ok"} ou {"status": "error", "message": "..."},
               suivie de la fermeture de la connexion (fin de la réponse)

Lancement (cf. README) :
    claude mcp add blender-mcp -- uv run --directory <chemin> blender_mcp_server.py
"""

import json
import socket

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("BlenderBridge")

# Adresse du serveur socket lancé dans Blender (doit correspondre à
# HOST / PORT dans blender_server.py). 127.0.0.1 = machine locale uniquement.
BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876


def _envoyer_a_blender(code_python: str) -> str:
    """Envoie un script à Blender via TCP et traduit la réponse en texte.

    Fonction interne (non décorée) : elle n'est pas visible par le client MCP.
    Elle renvoie toujours une chaîne, y compris en cas d'erreur, afin que le
    LLM reçoive un message exploitable plutôt qu'un plantage.
    """
    try:
        # Le "with" garantit la fermeture de la socket, même en cas d'erreur.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10.0)  # évite de bloquer indéfiniment si Blender ne répond pas
            s.connect((BLENDER_HOST, BLENDER_PORT))

            # Sérialisation JSON puis encodage en octets : une socket
            # transporte des bytes, pas des str.
            payload = json.dumps({"code": code_python})
            s.sendall(payload.encode("utf-8"))

            # TCP transporte un flux d'octets, pas des messages : il faut
            # signaler la fin de la requête. On ferme notre côté écriture ;
            # Blender lit alors jusqu'à la fin du flux.
            s.shutdown(socket.SHUT_WR)

            # Même principe pour la réponse : on lit jusqu'à ce que Blender
            # ferme la connexion (recv() renvoie b"").
            morceaux = []
            while morceau := s.recv(4096):
                morceaux.append(morceau)
            res = json.loads(b"".join(morceaux).decode("utf-8"))
            if res.get("status") == "ok":
                return "Commande exécutée avec succès dans Blender."
            return f"Erreur Blender : {res.get('message')}"
    except ConnectionRefusedError:
        # Cas le plus fréquent : Blender n'est pas lancé, ou le script
        # d'écoute n'a pas été exécuté. On donne une consigne claire.
        return (
            "Erreur : Impossible de joindre Blender. Assure-toi que Blender est "
            "ouvert et que le script d'écoute socket est lancé (port 9876)."
        )
    except Exception as e:
        return f"Erreur réseau : {e}"


# ---------------------------------------------------------------------
# Deux styles d'outils, à comparer
# ---------------------------------------------------------------------

# Style 1 : outil GÉNÉRIQUE. Le LLM écrit lui-même le code `bpy`.
#   + Très puissant : tout ce que Blender sait faire est accessible.
#   - Le LLM doit bien connaître l'API bpy ; les erreurs sont plus fréquentes.
#   - Sécurité : c'est de l'exécution de code arbitraire. Acceptable en
#     local pour une démo, à proscrire tel quel en production.
@mcp.tool()
def executer_script_blender(code_python: str) -> str:
    """Exécute un script Python arbitraire dans l'instance active de Blender.

    Permet de créer des objets, modifier des matériaux, positionner la caméra
    ou manipuler la scène via le module `bpy`.
    """
    return _envoyer_a_blender(code_python)


# Style 2 : outil SPÉCIALISÉ. Le LLM ne fournit que des paramètres typés,
# le serveur génère le code.
#   + Plus fiable et plus sûr : le LLM ne peut faire que ce qui est prévu.
#   + Les paramètres par défaut (position_x=0.0...) deviennent des arguments
#     optionnels dans le JSON Schema publié.
#   - Moins flexible : il faut un outil par cas d'usage.
@mcp.tool()
def creer_objet_primitif(type_objet: str, nom: str, position_x: float = 0.0, position_y: float = 0.0, position_z: float = 0.0) -> str:
    """Crée une primitive géométrique simple dans la scène 3D.

    Types supportés : 'cube', 'sphere', 'cylinder', 'plane'.
    """
    # Table de correspondance : nom "métier" -> opérateur Blender.
    # Elle sert aussi de liste blanche : tout autre type est refusé.
    mapping = {
        "cube": "bpy.ops.mesh.primitive_cube_add",
        "sphere": "bpy.ops.mesh.primitive_uv_sphere_add",
        "cylinder": "bpy.ops.mesh.primitive_cylinder_add",
        "plane": "bpy.ops.mesh.primitive_plane_add",
    }
    op = mapping.get(type_objet.lower())
    if not op:
        # Message d'erreur "actionnable" : on rappelle les valeurs valides.
        return f"Type d'objet inconnu : {type_objet}. Choisir parmi {list(mapping.keys())}."

    # Génération du script à envoyer : ajout de la primitive, puis
    # renommage de l'objet actif (celui qui vient d'être créé).
    # {nom!r} insère repr(nom), c'est-à-dire un littéral Python correctement
    # échappé : un nom comme "L'arbre" ne casse pas le script (et ne permet
    # pas d'y injecter du code).
    script = (
        f"{op}(location=({position_x}, {position_y}, {position_z})); "
        f"bpy.context.active_object.name = {nom!r}"
    )
    return _envoyer_a_blender(script)


if __name__ == "__main__":
    mcp.run(transport="stdio")
