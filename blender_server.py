"""
Script d'écoute à exécuter DANS Blender (onglet "Scripting", puis Alt + P).

Objectif pédagogique
--------------------
Ce fichier n'est PAS un serveur MCP. C'est la seconde moitié du pont
décrit dans blender_mcp_server.py :

    blender_mcp_server.py  --(TCP 127.0.0.1:9876)-->  ce script (dans Blender)

Blender embarque son propre interpréteur Python, qui seul donne accès au
module `bpy` (l'API de Blender). On ouvre donc une petite socket TCP dans
Blender : elle reçoit du code Python en JSON et l'exécute.

Deux contraintes de threads, deux mécanismes :
  1. Si la boucle `accept()` tournait dans le thread principal, l'interface
     de Blender serait gelée. On la lance donc dans un thread d'arrière-plan.
  2. En revanche, l'API `bpy` n'est PAS thread-safe : le code reçu doit être
     exécuté sur le thread principal. Les threads réseau déposent donc le
     code dans une file d'attente (queue.Queue), et une fonction enregistrée
     avec `bpy.app.timers.register(...)` la dépile régulièrement depuis le
     thread principal.

Protocole de lecture :
  Une socket TCP transporte un flux d'octets, pas des "messages" : un seul
  recv() peut ne renvoyer qu'une partie des données. Convention adoptée ici :
  le client ferme son côté écriture après l'envoi (shutdown), et on lit
  jusqu'à la fin du flux (recv() renvoie b"").

Sécurité :
  Ce script exécute (exec) tout code reçu. Il n'écoute que sur 127.0.0.1
  (machine locale), mais reste réservé à un usage de démonstration.
"""

import bpy  # disponible uniquement dans l'interpréteur Python de Blender
import json
import queue
import socket
import threading

# Doivent correspondre à BLENDER_HOST / BLENDER_PORT dans blender_mcp_server.py.
HOST = "127.0.0.1"
PORT = 9876

# Délai maximal d'attente de l'exécution par le thread principal.
DELAI_EXECUTION = 10.0

# File des scripts à exécuter. Chaque élément est un couple
# (code, file_reponse) : le thread principal dépose le résultat dans
# file_reponse, que le thread réseau attend.
_taches = queue.Queue()


def _recevoir_tout(conn) -> str:
    """Lit la socket jusqu'à la fin du flux et renvoie le texte complet."""
    morceaux = []
    while True:
        morceau = conn.recv(4096)
        if not morceau:  # b"" : le client a fermé son côté écriture
            break
        morceaux.append(morceau)
    return b"".join(morceaux).decode("utf-8")


def _executer_taches():
    """Exécute les scripts en attente. Appelée par Blender sur le thread principal.

    La valeur renvoyée est le délai (en secondes) avant le prochain appel :
    renvoyer 0.1 fait de cette fonction une boucle de "polling" légère.
    """
    while True:
        try:
            code, file_reponse = _taches.get_nowait()
        except queue.Empty:
            break
        try:
            # On fournit un espace de noms où `bpy` est déjà importé, pour que
            # le code envoyé puisse l'utiliser directement sans "import bpy".
            exec(code, {"bpy": bpy})
            file_reponse.put({"status": "ok"})
        except Exception as e:
            # Toute erreur dans le script bpy est renvoyée au serveur MCP,
            # qui la transmettra au LLM.
            file_reponse.put({"status": "error", "message": str(e)})
    return 0.1


def handle_client(conn):
    """Traite UNE connexion (dans un thread réseau) : lit, délègue, répond."""
    try:
        data = _recevoir_tout(conn)
        if not data:
            return
        payload = json.loads(data)          # {"code": "..."}
        code = payload.get("code", "")

        # On ne fait PAS exec() ici (thread réseau) : on délègue au thread
        # principal et on attend sa réponse.
        file_reponse = queue.Queue(maxsize=1)
        _taches.put((code, file_reponse))
        try:
            reponse = file_reponse.get(timeout=DELAI_EXECUTION)
        except queue.Empty:
            reponse = {"status": "error", "message": "Délai d'exécution dépassé dans Blender."}

        conn.sendall(json.dumps(reponse).encode("utf-8"))
    except Exception as e:
        # Erreurs côté réseau / JSON invalide.
        conn.sendall(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))
    finally:
        # Fermer la connexion signale au client la fin de la réponse.
        conn.close()


def start_server():
    """Ouvre la socket d'écoute et accepte les connexions en boucle."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR permet de relancer le script sans attendre que l'OS
    # libère le port (sinon : "Address already in use").
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)  # jusqu'à 5 connexions en attente
    # Ce print s'affiche dans la console système de Blender.
    print(f"[MCP-Blender] Écoute sur {HOST}:{PORT}")

    while True:
        conn, _ = server.accept()  # bloque jusqu'à la prochaine connexion
        # Un thread par client, pour ne pas bloquer la boucle d'écoute.
        # daemon=True : le thread s'arrête avec Blender.
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()


# Le timer tourne sur le thread principal de Blender et exécute les scripts.
# persistent=True : il survit au chargement d'un autre fichier .blend.
bpy.app.timers.register(_executer_taches, persistent=True)

# Démarrage du réseau en arrière-plan : le script rend la main immédiatement
# et l'interface de Blender reste utilisable.
threading.Thread(target=start_server, daemon=True).start()
