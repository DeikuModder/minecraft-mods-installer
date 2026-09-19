import atexit
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
import remote
import server
import sync
import tunnel
import updates
import version

TITULO = "Instalador de Mods de Minecraft"
APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
DEFAULT_B = os.path.join(APPDATA, ".minecraft", "mods")
TERMINAL = ("terminado", "error", "respuesta", "resumen", "servidor", "tunel", "update_listo", "upd_check", "uno_ok")


class TaskQueue:
    def __init__(self, root, on_event, poll_ms=100):
        self.root = root
        self.on_event = on_event
        self.cola = queue.Queue()
        self.poll_ms = poll_ms
        self.busy = False

    def submit(self, fn):
        if self.busy:
            return False
        self.busy = True
        self.root.after(self.poll_ms, self._poll)
        threading.Thread(target=self._worker, args=(fn,), daemon=True).start()
        return True

    def _worker(self, fn):
        try:
            fn(self.cola)
        except Exception as e:
            self.cola.put({"tipo": "error", "detalle": str(e)})

    def _poll(self):
        terminal = False
        try:
            while True:
                msg = self.cola.get_nowait()
                self.on_event(msg)
                if msg.get("tipo") in TERMINAL:
                    terminal = True
        except queue.Empty:
            pass
        if terminal:
            self.busy = False
            return
        if self.busy:
            self.root.after(self.poll_ms, self._poll)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITULO)
        self.resizable(False, False)

        cfg = config.load()
        self.rol = cfg.get("role", "anfitrion")
        self._tarea = ""
        self._after_guardar = None
        self.var_a = tk.StringVar(value=cfg.get("folder_a", ""))
        self.var_b = tk.StringVar(value=cfg.get("folder_b") or DEFAULT_B)
        self.var_origen = tk.StringVar(value=cfg.get("last_origin_url", ""))
        self.var_hostname = tk.StringVar(value=cfg.get("hostname", ""))
        self.var_url_publica = tk.StringVar()

        self._server = None
        self._tunnel_proc = None
        self._servidor_activo = False
        self._cambios = None
        self._manifest = []

        self.task_local = TaskQueue(self, self._evento_local)
        self.task_sync = TaskQueue(self, self._evento_sync)
        self.task_updates = TaskQueue(self, self._evento_updates)
        self._upd_silencioso = False
        self._upd_info = None

        self._crear_ui()
        self.var_a.trace_add("write", lambda *_: self._actualizar_boton())
        self.var_b.trace_add("write", lambda *_: self._actualizar_boton())
        for var in (self.var_a, self.var_b, self.var_hostname, self.var_origen):
            var.trace_add("write", lambda *_: self._programar_guardado())
        self._actualizar_boton()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self._limpiar_temporales_updates()
        self.after(1500, self._chequeo_inicial)

    def _limpiar_temporales_updates(self):
        if not getattr(sys, "frozen", False):
            return
        for nombre in ("InstaladorMods_Updater", "InstaladorMods_updates"):
            shutil.rmtree(os.path.join(os.environ.get("TEMP", "."), nombre), ignore_errors=True)

    def _crear_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.tab_local = ttk.Frame(self.notebook, padding=14)
        self.tab_sync = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.tab_local, text="Instalador local")
        self.notebook.add(self.tab_sync, text="Sincronizar")

        self._crear_ui_local()
        self._crear_ui_sync()

    def _crear_ui_local(self):
        frm = self.tab_local

        ttk.Label(frm, text="Carpeta A: mods descargados").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        fila_a = ttk.Frame(frm)
        fila_a.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Entry(fila_a, textvariable=self.var_a, width=50).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(fila_a, text="Examinar...", command=self._examinar_a).pack(
            side="left", padx=(6, 0)
        )

        ttk.Label(frm, text="Carpeta B: mods de .minecraft (mods)").grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        fila_b = ttk.Frame(frm)
        fila_b.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        ttk.Entry(fila_b, textvariable=self.var_b, width=50).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(fila_b, text="Examinar...", command=self._examinar_b).pack(
            side="left", padx=(6, 0)
        )

        self.btn = ttk.Button(frm, command=self._ejecutar, width=30)
        self.btn.grid(row=4, column=0, sticky="ew")

        self.progress = ttk.Progressbar(frm, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=5, column=0, sticky="ew", pady=(14, 6))

        self.lbl_estado = ttk.Label(frm, text="", anchor="w", foreground="#555")
        self.lbl_estado.grid(row=6, column=0, sticky="ew")

        fila_upd = ttk.Frame(frm)
        fila_upd.grid(row=7, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(fila_upd, text="Versión " + version.APP_VERSION).pack(side="left")
        self.btn_actualizar = ttk.Button(
            fila_upd, text="Actualizar ahora", command=self._actualizar_ya
        )
        self.btn_update = ttk.Button(
            fila_upd, text="Buscar actualizaciones", command=self._buscar_actualizaciones
        )
        self.btn_update.pack(side="right")

        self.lbl_update = ttk.Label(frm, text="", anchor="w", foreground="#333")
        self.lbl_update.grid(row=8, column=0, sticky="ew", pady=(4, 0))

        self.progress_upd = ttk.Progressbar(frm, mode="determinate", maximum=100, value=0)
        self.progress_upd.grid(row=9, column=0, sticky="ew", pady=(4, 0))

    def _crear_ui_sync(self):
        barra = ttk.Frame(self.tab_sync)
        barra.pack(fill="x", pady=(0, 10))
        self.lbl_modo = ttk.Label(barra, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_modo.pack(side="left")
        self.btn_cambiar_rol = ttk.Button(barra, command=self._cambiar_rol)
        self.btn_cambiar_rol.pack(side="right")

        self._crear_ui_anfitrion()
        self._crear_ui_consumidor()
        self._mostrar_vista(self.rol)

    def _crear_ui_anfitrion(self):
        sec_a = ttk.LabelFrame(self.tab_sync, text="Compartir mis mods (anfitrión)", padding=10)
        self.sec_anfitrion = sec_a

        ttk.Label(sec_a, text="Carpeta de mods (origen):").grid(row=0, column=0, sticky="w")
        fila_b = ttk.Frame(sec_a)
        fila_b.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Entry(fila_b, textvariable=self.var_b, width=44).pack(side="left", fill="x", expand=True)
        ttk.Button(fila_b, text="Examinar...", command=self._examinar_b).pack(side="left", padx=(6, 0))

        ttk.Label(sec_a, text="Hostname público:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(sec_a, textvariable=self.var_hostname, width=40).grid(
            row=2, column=1, sticky="w", padx=(6, 0), pady=(8, 0)
        )

        fila_btn = ttk.Frame(sec_a)
        fila_btn.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.btn_setup = ttk.Button(fila_btn, text="Configurar túnel", command=self._setup)
        self.btn_setup.pack(side="left")
        self.btn_iniciar = ttk.Button(fila_btn, text="Iniciar servidor y túnel", command=self._iniciar)
        self.btn_iniciar.pack(side="left", padx=(6, 0))
        self.btn_detener = ttk.Button(fila_btn, text="Detener", command=self._detener, state="disabled")
        self.btn_detener.pack(side="left", padx=(6, 0))

        fila_url = ttk.Frame(sec_a)
        fila_url.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Entry(fila_url, textvariable=self.var_url_publica, state="readonly", width=40).pack(
            side="left"
        )
        ttk.Button(fila_url, text="Copiar", command=self._copiar_url).pack(side="left", padx=(6, 0))

        self.lbl_downloads = ttk.Label(sec_a, text="0 descargas", foreground="#555")
        self.lbl_downloads.grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.lbl_estado_servidor = ttk.Label(sec_a, text="", anchor="w", foreground="#555")
        self.lbl_estado_servidor.grid(row=5, column=1, sticky="w", padx=(6, 0), pady=(6, 0))

    def _crear_ui_consumidor(self):
        sec_b = ttk.LabelFrame(self.tab_sync, text="Conectar a un origen (consumidor)", padding=10)
        self.sec_consumidor = sec_b

        ttk.Label(sec_b, text="Carpeta destino (mods):").grid(row=0, column=0, sticky="w")
        fila_dest = ttk.Frame(sec_b)
        fila_dest.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Entry(fila_dest, textvariable=self.var_b, width=44).pack(side="left", fill="x", expand=True)
        ttk.Button(fila_dest, text="Examinar...", command=self._examinar_b).pack(side="left", padx=(6, 0))

        fila_origin = ttk.Frame(sec_b)
        fila_origin.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Entry(fila_origin, textvariable=self.var_origen, width=40).pack(
            side="left", fill="x", expand=True
        )
        self.btn_comprobar = ttk.Button(fila_origin, text="Comprobar cambios", command=self._comprobar)
        self.btn_comprobar.pack(side="left", padx=(6, 0))

        self.lbl_resultado = ttk.Label(sec_b, text="", anchor="w", foreground="#555")
        self.lbl_resultado.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(sec_b, text="Mods nuevos que se descargarán:", anchor="w").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        marco_lista = ttk.Frame(sec_b)
        marco_lista.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        scroll = ttk.Scrollbar(marco_lista, orient="vertical")
        self.lista_mods = tk.Listbox(
            marco_lista,
            height=6,
            yscrollcommand=scroll.set,
            activestyle="none",
            exportselection=False,
            width=48,
        )
        scroll.config(command=self.lista_mods.yview)
        scroll.pack(side="right", fill="y")
        self.lista_mods.pack(side="left", fill="both", expand=True)
        self.lista_mods.bind("<Button-3>", self._menu_derecho_mods)
        self.menu_mod = tk.Menu(self, tearoff=0)
        self.menu_mod.add_command(label="Descargar este mod ahora", command=self._descargar_mod_seleccionado)

        sec_borrar = ttk.LabelFrame(sec_b, text="Mods que se eliminarán al sincronizar:", padding=6)
        self.sec_borrar = sec_borrar
        marco_borrar = ttk.Frame(sec_borrar)
        marco_borrar.pack(fill="both", expand=True)
        scroll_b = ttk.Scrollbar(marco_borrar, orient="vertical")
        self.lista_borrar = tk.Listbox(
            marco_borrar,
            height=3,
            yscrollcommand=scroll_b.set,
            activestyle="none",
            exportselection=False,
            width=48,
            foreground="#a00",
        )
        scroll_b.config(command=self.lista_borrar.yview)
        scroll_b.pack(side="right", fill="y")
        self.lista_borrar.pack(side="left", fill="both", expand=True)

        fila_cmd = ttk.Frame(sec_b)
        fila_cmd.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.btn_sincronizar = ttk.Button(
            fila_cmd, text="Descargar mods", command=self._sincronizar, state="disabled"
        )
        self.btn_sincronizar.pack(side="left")

        self.progress_sync = ttk.Progressbar(sec_b, mode="determinate", maximum=100, value=0)
        self.progress_sync.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(10, 6))

        self.lbl_estado_sync = ttk.Label(sec_b, text="", anchor="w", foreground="#555")
        self.lbl_estado_sync.grid(row=9, column=0, columnspan=2, sticky="ew")
        self.lbl_archivo_sync = ttk.Label(sec_b, text="", anchor="w", foreground="#555")
        self.lbl_archivo_sync.grid(row=10, column=0, columnspan=2, sticky="ew")

    def _mostrar_vista(self, rol):
        self.sec_anfitrion.pack_forget()
        self.sec_consumidor.pack_forget()
        if rol == "consumidor":
            self.sec_consumidor.pack(fill="x")
            self.lbl_modo.config(text="Modo actual: Consumidor")
            self.btn_cambiar_rol.config(text="Cambiar a Anfitrión")
        else:
            self.sec_anfitrion.pack(fill="x")
            self.lbl_modo.config(text="Modo actual: Anfitrión")
            self.btn_cambiar_rol.config(text="Cambiar a Consumidor")
        config.save({"role": rol})

    def _cambiar_rol(self):
        if self.task_sync.busy:
            return
        self.rol = "consumidor" if self.rol != "consumidor" else "anfitrion"
        self._mostrar_vista(self.rol)
        self._guardar_campos()

    def _examinar_a(self):
        ruta = filedialog.askdirectory(title="Selecciona la carpeta de mods descargados (A)")
        if ruta:
            self.var_a.set(ruta)

    def _examinar_b(self):
        ruta = filedialog.askdirectory(title="Selecciona la carpeta de mods de .minecraft (B)")
        if ruta:
            self.var_b.set(ruta)

    def _actualizar_boton(self):
        if self.task_local.busy:
            return
        b = self.var_b.get().strip()
        hay_mods = sync.count_files(b) > 0 if os.path.isdir(b) else False
        self.btn.config(text="ACTUALIZAR" if hay_mods else "INSTALAR")

    def _mostrar_mensaje(self, text, ok=True):
        (messagebox.showinfo if ok else messagebox.showerror)(TITULO, text)

    def _copiar_url(self):
        url = self.var_url_publica.get()
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)

    def _al_cerrar(self):
        tunnel.stop(self._tunnel_proc)
        self._tunnel_proc = None
        if self._server is not None:
            s = self._server
            self._server = None
            threading.Thread(target=s.shutdown, daemon=True).start()
        if self._after_guardar is not None:
            self.after_cancel(self._after_guardar)
            self._after_guardar = None
        self._guardar_campos()
        self.destroy()

    def _programar_guardado(self):
        if self._after_guardar is not None:
            self.after_cancel(self._after_guardar)
        self._after_guardar = self.after(800, self._guardar_campos)

    def _guardar_campos(self):
        self._after_guardar = None
        config.save(
            {
                "folder_a": self.var_a.get().strip(),
                "folder_b": self.var_b.get().strip(),
                "hostname": self.var_hostname.get().strip(),
                "last_origin_url": self.var_origen.get().strip(),
                "role": self.rol,
            }
        )

    def _rearmar_sync(self):
        self.btn_comprobar.config(state="normal")
        self.btn_sincronizar.config(state="normal")

    def _validar_b(self):
        b = self.var_b.get().strip()
        if not os.path.isdir(b):
            crear = messagebox.askyesno(
                TITULO,
                f"La carpeta B no existe:\n{b}\n\n¿Deseas crearla?",
            )
            if not crear:
                return None
            os.makedirs(b)
        return b

    def _ejecutar(self):
        if self.task_local.busy:
            return
        a = self.var_a.get().strip()
        b = self._validar_b()
        if b is None:
            return
        if not os.path.isdir(a):
            self._mostrar_mensaje(
                "La carpeta A no existe o no es una carpeta válida.\n"
                "Verifica la ruta de los mods descargados.",
                ok=False,
            )
            return
        self.btn.config(state="disabled")
        self.progress.config(value=0)
        self.lbl_estado.config(text="Comparando carpetas...")
        if not self.task_local.submit(lambda cola, a=a, b=b: self._trabajo_local(cola, a, b)):
            self.btn.config(state="normal")

    def _trabajo_local(self, cola, a, b):
        src = sync.scan_sizes(a)
        dst = sync.scan_sizes(b) if os.path.isdir(b) else {}
        to_copy, to_overwrite = sync.plan_sync(a, b)
        total = len(to_copy) + len(to_overwrite)
        iguales = sum(1 for n, s in src.items() if n in dst and dst[n] == s)
        if total == 0:
            cola.put({"tipo": "resumen", "instalados": 0, "actualizados": 0, "presentes": iguales})
            return

        def cb(done, total, name):
            cola.put({"tipo": "progreso", "hecho": done, "total": total, "archivo": name})

        r = sync.execute_plan(a, b, to_copy, to_overwrite, progress_cb=cb)
        cola.put(
            {
                "tipo": "resumen",
                "instalados": len(r.installed),
                "actualizados": len(r.updated),
                "presentes": iguales,
            }
        )

    def _evento_local(self, msg):
        t = msg["tipo"]
        if t == "progreso":
            total = msg["total"]
            pct = min(100.0, (msg["hecho"] / total) * 100.0) if total else 0.0
            self.progress.config(value=pct)
            self.lbl_estado.config(text=f"Copiando: {msg['archivo']} ({msg['hecho']}/{total})")
        elif t == "resumen":
            self.progress.config(value=100)
            self.btn.config(state="normal")
            self._actualizar_boton()
            self._mostrar_mensaje(
                f"¡Listo!\n\n"
                f"Instalados: {msg['instalados']}\n"
                f"Actualizados: {msg['actualizados']}\n"
                f"Ya presentes: {msg['presentes']}"
            )
        elif t == "error":
            self.btn.config(state="normal")
            self._actualizar_boton()
            self.progress.config(value=0)
            self.lbl_estado.config(text="")
            self._mostrar_mensaje(f"Ocurrió un error durante la copia:\n\n{msg['detalle']}", ok=False)

    def _setup(self):
        if self.task_sync.busy:
            return
        hostname = self.var_hostname.get().strip()
        if not hostname:
            self._mostrar_mensaje(
                "Escribe el hostname público en el campo 'Hostname público' "
                "(ej. mods.tudominio.com).",
                ok=False,
            )
            return
        self.btn_setup.config(state="disabled")
        self.lbl_estado_servidor.config(text="Configurando túnel...")
        self._tarea = "setup"
        self.task_sync.submit(lambda cola, h=hostname: self._trabajo_setup(cola, h))

    def _trabajo_setup(self, cola, hostname):
        def aviso(t):
            cola.put({"tipo": "aviso", "texto": t})

        aviso("Localizando cloudflared...")
        exe = tunnel.ensure_cloudflared(
            progress_cb=lambda p: aviso(f"Descargando cloudflared... {int(p * 100)}%")
        )
        cfg = config.load()
        uuid = tunnel.preparar(exe, cfg["tunnel_name"], hostname, aviso=aviso)
        config.save({"hostname": hostname})
        cola.put({"tipo": "tunel", "hostname": hostname, "uuid": uuid})

    def _iniciar(self):
        if self.task_sync.busy or self._servidor_activo:
            return
        hostname = self.var_hostname.get().strip()
        if not hostname:
            self._mostrar_mensaje(
                "Escribe el hostname público (ej. mods.tudominio.com) y pulsa "
                "'Configurar túnel' primero.",
                ok=False,
            )
            return
        b = self.var_b.get().strip()
        self.btn_iniciar.config(state="disabled")
        self.lbl_estado_servidor.config(text="Preparando servidor y túnel...")
        self._tarea = "iniciar"
        self.task_sync.submit(lambda cola, h=hostname, fb=b: self._trabajo_compartir(cola, fb, h))

    def _trabajo_compartir(self, cola, b, hostname):
        def aviso(t):
            cola.put({"tipo": "aviso", "texto": t})

        if not os.path.isdir(b):
            raise RuntimeError(
                "La carpeta B (mods) no existe. Crea o selecciona la carpeta de mods primero."
            )
        aviso("Localizando cloudflared...")
        exe = tunnel.ensure_cloudflared(
            progress_cb=lambda p: aviso(f"Descargando cloudflared... {int(p * 100)}%")
        )
        cfg = config.load()
        uuid = tunnel.preparar(exe, cfg["tunnel_name"], hostname, aviso=aviso)
        config.save({"hostname": hostname})
        aviso("Iniciando servidor HTTP local...")
        srv = server.ModsServer(("127.0.0.1", 0), b)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        puerto = srv.server_address[1]
        self._server = srv
        yml = os.path.join(config.APP_DIR, "tunnel.yml")
        tunnel.write_tunnel_config(yml, uuid, hostname, puerto)
        log = os.path.join(config.APP_DIR, "cloudflared.log")
        pos = os.path.getsize(log) if os.path.exists(log) else 0
        aviso("Levantando el túnel de Cloudflare...")
        proc = tunnel.start_tunnel(exe, cfg["tunnel_name"], yml, log)
        self._tunnel_proc = proc
        aviso("Esperando a que el túnel se conecte...")
        if not tunnel.wait_connection(log, since=pos, timeout=120, proc=proc):
            raise RuntimeError(
                "El túnel no conectó. Comprueba el hostname/DNS y vuelve a intentarlo."
            )
        cola.put(
            {
                "tipo": "servidor",
                "url": "https://" + hostname,
                "mods": sync.count_files(b),
            }
        )

    def _detener(self):
        tunnel.stop(self._tunnel_proc)
        self._tunnel_proc = None
        if self._server is not None:
            s = self._server
            self._server = None
            threading.Thread(target=s.shutdown, daemon=True).start()
        self._servidor_activo = False
        self.var_url_publica.set("")
        self.lbl_estado_servidor.config(text="Servidor detenido")
        self.lbl_downloads.config(text="0 descargas")
        self.btn_iniciar.config(state="normal")
        self.btn_detener.config(state="disabled")

    def _tick_servidor(self):
        if self._servidor_activo and self._server is not None:
            self.lbl_downloads.config(text=f"{self._server.downloads} descargas")
            self.after(2000, self._tick_servidor)

    def _comprobar(self):
        if self.task_sync.busy:
            return
        b = self._validar_b()
        if b is None:
            return
        url = self.var_origen.get().strip()
        if not url:
            self._mostrar_mensaje("Escribe la URL del origen (ej. https://mods.tudominio.com).", ok=False)
            return
        self.btn_comprobar.config(state="disabled")
        self.btn_sincronizar.config(state="disabled")
        self.lbl_resultado.config(text="")
        self.lbl_estado_sync.config(text="Consultando el origen...")
        self._tarea = "comprobar"
        self._guardar_campos()
        self.task_sync.submit(lambda cola, u=url, fb=b: self._trabajo_comprobar(cola, u, fb))

    def _trabajo_comprobar(self, cola, url, b):
        def aviso(t):
            cola.put({"tipo": "aviso_cliente", "texto": t})

        aviso("Consultando el manifest del origen...")
        manifest = remote.fetch_manifest(url)
        if not manifest:
            raise RuntimeError("El origen no tiene mods.")
        locales = sync.scan_sizes(b) if os.path.isdir(b) else {}
        descargar, actualizar, eliminar, iguales = remote.compare(locales, manifest)
        config.save({"last_origin_url": url})
        cola.put(
            {
                "tipo": "respuesta",
                "descargar": descargar,
                "actualizar": actualizar,
                "eliminar": eliminar,
                "iguales": iguales,
                "mods": len(manifest),
                "manifest": manifest,
            }
        )

    def _sincronizar(self):
        if self.task_sync.busy:
            return
        if not (self._cambios and (self._cambios[0] or self._cambios[1] or self._cambios[2])):
            self._mostrar_mensaje("No hay mods pendientes. Pulsa 'Comprobar cambios' primero.")
            return
        descargar, actualizar, eliminar = self._cambios
        url = self.var_origen.get().strip()
        b = self.var_b.get().strip()
        self.btn_sincronizar.config(state="disabled")
        self.btn_comprobar.config(state="disabled")
        self.progress_sync.config(value=0)
        self.lbl_archivo_sync.config(text="")
        self.lbl_estado_sync.config(text="Descargando...")
        self._tarea = "descargar"
        self._guardar_campos()
        self.task_sync.submit(
            lambda cola, u=url, fb=b, d=descargar, ac=actualizar, el=eliminar: self._trabajo_sincronizar(
                cola, u, fb, d, ac, el
            )
        )

    def _trabajo_sincronizar(self, cola, url, b, descargar, actualizar, eliminar):
        def cb(done, total, name):
            cola.put({"tipo": "progreso", "hecho": done, "total": total, "archivo": name})

        if eliminar:
            cola.put({"tipo": "aviso_cliente", "texto": "Eliminando mods no presentes en el origen..."})
        listos, actualizados, eliminados = remote.sync_from_remote(
            url, b, descargar, actualizar, eliminar, progress_cb=cb
        )
        cola.put(
            {
                "tipo": "resumen",
                "instalados": len(listos),
                "actualizados": len(actualizados),
                "eliminados": eliminados,
            }
        )

    def _menu_derecho_mods(self, event):
        idx = self.lista_mods.nearest(event.y)
        if idx < 0 or idx >= self.lista_mods.size():
            return
        estado = "disabled" if self.task_sync.busy else "normal"
        self.menu_mod.entryconfig(0, state=estado)
        self.lista_mods.selection_clear(0, tk.END)
        self.lista_mods.selection_set(idx)
        self.lista_mods.activate(idx)
        try:
            self.menu_mod.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu_mod.grab_release()

    def _descargar_mod_seleccionado(self):
        sel = self.lista_mods.curselection()
        if not sel:
            return
        self._descargar_mod(self.lista_mods.get(sel[0]))

    def _descargar_mod(self, nombre):
        if self.task_sync.busy:
            return
        b = self._validar_b()
        if b is None:
            return
        url = self.var_origen.get().strip()
        if not url:
            self._mostrar_mensaje("Escribe la URL del origen (ej. https://mods.tudominio.com).", ok=False)
            return
        self.btn_sincronizar.config(state="disabled")
        self.btn_comprobar.config(state="disabled")
        self.progress_sync.config(value=0)
        self.lbl_archivo_sync.config(text="")
        self.lbl_estado_sync.config(text="Descargando...")
        self._tarea = "descargar_uno"
        self._guardar_campos()
        self.task_sync.submit(
            lambda cola, u=url, fb=b, n=nombre: self._trabajo_descargar_uno(cola, u, fb, n)
        )

    def _trabajo_descargar_uno(self, cola, url, b, nombre):
        def cb(leido, total):
            cola.put(
                {
                    "tipo": "progreso_uno",
                    "pct": (100.0 * leido / total) if total else 0.0,
                    "nombre": nombre,
                }
            )

        remote.descargar_uno(url, b, nombre, progreso=cb)
        locales = sync.scan_sizes(b) if os.path.isdir(b) else {}
        cola.put({"tipo": "uno_ok", "nombre": nombre, "locales": locales})

    def _pintar_resultado(self, desc, act, eli):
        if desc or act or eli:
            partes = []
            if desc:
                partes.append(f"{len(desc)} mods nuevos encontrados")
            if act:
                partes.append(f"{len(act)} para actualizar")
            if eli:
                partes.append(f"{len(eli)} para eliminar")
            self.lbl_resultado.config(text=", ".join(partes) + ".")
        else:
            self.lbl_resultado.config(text="No hay cambios: los mods están al día.")
        self.lista_mods.delete(0, tk.END)
        for nombre in desc:
            self.lista_mods.insert(tk.END, nombre)
        self.lista_borrar.delete(0, tk.END)
        for nombre in eli:
            self.lista_borrar.insert(tk.END, nombre)
        if eli:
            self.sec_borrar.grid()
        else:
            self.sec_borrar.grid_remove()

    def _evento_sync(self, msg):
        t = msg["tipo"]
        if t == "aviso":
            self.lbl_estado_servidor.config(text=msg["texto"])
        elif t == "aviso_cliente":
            self.lbl_estado_sync.config(text=msg["texto"])
        elif t == "progreso":
            total = msg["total"]
            pct = min(100.0, (msg["hecho"] / total) * 100.0) if total else 0.0
            self.progress_sync.config(value=pct)
            self.lbl_estado_sync.config(text="Descargando...")
            self.lbl_archivo_sync.config(text=f"{msg['archivo']} ({msg['hecho']}/{total})")
        elif t == "respuesta":
            self._manifest = msg.get("manifest") or []
            desc, act, eli = msg["descargar"], msg["actualizar"], msg["eliminar"]
            self._cambios = (desc, act, eli)
            self._pintar_resultado(desc, act, eli)
            self._rearmar_sync()
        elif t == "progreso_uno":
            self.progress_sync.config(value=msg["pct"])
            self.lbl_estado_sync.config(text="Descargando...")
            self.lbl_archivo_sync.config(
                text=f"{msg['nombre']} ({int(msg['pct'])}% de la descarga individual)"
            )
        elif t == "uno_ok":
            self.progress_sync.config(value=100)
            desc, act, eli, iguales = remote.compare(msg["locales"], self._manifest)
            self._cambios = (desc, act, eli)
            self._pintar_resultado(desc, act, eli)
            self._rearmar_sync()
            self._mostrar_mensaje(f"Descargado: {msg['nombre']}")
        elif t == "resumen":
            self.progress_sync.config(value=100)
            self.sec_borrar.grid_remove()
            self._cambios = None
            self._manifest = []
            self._rearmar_sync()
            eliminados = msg.get("eliminados") or []
            texto = (
                f"¡Descarga completada!\n\nDescargados: {msg['instalados']}\n"
                f"Actualizados: {msg['actualizados']}"
            )
            if eliminados:
                texto += f"\nEliminados: {len(eliminados)}"
            self._mostrar_mensaje(texto)
        elif t == "servidor":
            self._servidor_activo = True
            self.var_url_publica.set(msg["url"])
            self.lbl_estado_servidor.config(text=f"Conectado: {msg['url']} — {msg['mods']} mods")
            self.btn_iniciar.config(state="disabled")
            self.btn_detener.config(state="normal")
            self.after(2000, self._tick_servidor)
        elif t == "tunel":
            self.lbl_estado_servidor.config(
                text=f"Túnel listo: https://{msg['hostname']}. Ya puedes pulsar 'Iniciar servidor y túnel'."
            )
            self.btn_setup.config(state="normal")
        elif t == "error":
            if self._tarea == "comprobar":
                self._cambios = None
            self.btn_setup.config(state="normal")
            self.btn_iniciar.config(state="normal")
            self.progress_sync.config(value=0)
            self.lbl_estado_sync.config(text="")
            self.lbl_archivo_sync.config(text="")
            self._rearmar_sync()
            self._mostrar_mensaje(msg["detalle"], ok=False)

    def _chequeo_inicial(self):
        if getattr(sys, "frozen", False):
            self._chequear_version(silencioso=True)

    def _chequear_version(self, silencioso=False):
        if self.task_updates.busy:
            return
        self._upd_silencioso = silencioso
        if not silencioso:
            self.btn_update.config(state="disabled")
            self.lbl_update.config(text="Buscando actualizaciones...")
        self.task_updates.submit(lambda cola: self._trabajo_chequear(cola))

    def _buscar_actualizaciones(self):
        self._chequear_version()

    def _trabajo_chequear(self, cola):
        try:
            info = updates.version_remota()
            hay = bool(info) and updates.comparar_semver(info["version"], version.APP_VERSION) > 0
            cola.put({"tipo": "upd_check", "info": info, "hay": hay})
        except Exception as e:
            cola.put({"tipo": "upd_check", "info": None, "hay": False, "error": str(e)})

    def _evento_updates(self, msg):
        t = msg["tipo"]
        if t == "upd_check":
            self.btn_update.config(state="normal")
            if msg.get("hay"):
                self._upd_info = msg["info"]
                v = msg["info"]["version"]
                if not self.btn_actualizar.winfo_ismapped():
                    self.btn_actualizar.pack(side="right", padx=(0, 6))
                self.lbl_update.config(
                    text=f"Nueva versión {v} disponible. Pulsa 'Actualizar ahora'.",
                    foreground="#0a0",
                )
                self._mostrar_pregunta_actualizar(msg["info"])
            else:
                self._upd_info = None
                if self.btn_actualizar.winfo_ismapped():
                    self.btn_actualizar.pack_forget()
                if msg.get("error"):
                    if self._upd_silencioso:
                        self.lbl_update.config(text="")
                    else:
                        self.lbl_update.config(text="")
                        self._mostrar_mensaje(
                            f"No se pudo comprobar actualizaciones:\n\n{msg['error']}", ok=False
                        )
                else:
                    self.lbl_update.config(
                        text="Estás en la última versión." if not self._upd_silencioso else ""
                    )
        elif t == "update_progreso":
            self.progress_upd.config(value=msg["pct"])
            self.lbl_update.config(text=f"Descargando la actualización... {int(msg['pct'])}%")
        elif t == "update_estado":
            self.lbl_update.config(text=msg["texto"])
        elif t == "update_listo":
            self.btn_update.config(state="normal")
            self.progress_upd.config(value=100)
            self._aplicar_cambio(msg["nuevo_dir"])
        elif t == "error":
            self.btn_update.config(state="normal")
            self._upd_info = None
            if self.btn_actualizar.winfo_ismapped():
                self.btn_actualizar.pack_forget()
            self.progress_upd.config(value=0)
            self.lbl_update.config(text="", foreground="#333")
            self._mostrar_mensaje(msg["detalle"], ok=False)

    def _actualizar_ya(self):
        if self._upd_info and not self.task_updates.busy:
            self._mostrar_pregunta_actualizar(self._upd_info)

    def _mostrar_pregunta_actualizar(self, info):
        if messagebox.askyesno(
            TITULO,
            f"Hay una nueva versión ({info['version']}).\n\n"
            "¿Descargar e instalar ahora?\n\n"
            "La aplicación se cerrará y se reiniciará automáticamente al terminar.",
        ):
            self.after(0, lambda i=info: self._aplicar_actualizacion(i))

    def _aplicar_actualizacion(self, info):
        if self.task_updates.busy:
            return
        self.btn_update.config(state="disabled")
        self.progress_upd.config(value=0)
        self.lbl_update.config(text="Descargando la actualización...", foreground="#333")
        self.task_updates.submit(lambda cola, i=info: self._trabajo_actualizar(cola, i))

    def _trabajo_actualizar(self, cola, info):
        base = os.path.join(os.environ.get("TEMP", "."), "InstaladorMods_updates")
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base)
        zip_ruta = os.path.join(base, "InstaladorMods.zip")
        updates.descargar(
            info["url"],
            zip_ruta,
            sha256=info.get("sha256"),
            progreso=lambda d, t: cola.put(
                {"tipo": "update_progreso", "pct": (100.0 * d / t) if t else 0}
            ),
        )
        cola.put({"tipo": "update_estado", "texto": "Extrayendo la actualización..."})
        extraido = os.path.join(base, "extraido")
        updates.extraer(zip_ruta, extraido)
        nuevo = updates.ubicar_exe(extraido)
        cola.put({"tipo": "update_listo", "nuevo_dir": nuevo})

    def _aplicar_cambio(self, nuevo_dir):
        if not getattr(sys, "frozen", False):
            self._mostrar_mensaje("En desarrollo el cambio se aplica al ejecutable empaquetado.")
            return
        destino = os.path.dirname(sys.executable)
        upd_base = os.path.join(os.environ.get("TEMP", "."), "InstaladorMods_Updater")
        shutil.rmtree(upd_base, ignore_errors=True)
        shutil.copytree(destino, upd_base)
        tmp_exe = os.path.join(upd_base, "InstaladorMods.exe")
        pid = os.getpid()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [tmp_exe, "--aplicar-update", nuevo_dir, destino, str(pid)],
            creationflags=flags,
            close_fds=True,
        )
        tunnel.stop(self._tunnel_proc)
        self._tunnel_proc = None
        if self._server is not None:
            s = self._server
            self._server = None
            threading.Thread(target=s.shutdown, daemon=True).start()
        self._guardar_campos()
        self.destroy()


_LOCK = os.path.join(config.APP_DIR, "app.lock")


def _pid_vivo(pid):
    try:
        sal = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return f'"{pid}"' in sal.stdout
    except Exception:
        return False


def _soltar_cerrojo():
    try:
        if os.path.exists(_LOCK) and open(_LOCK).read().strip() == str(os.getpid()):
            os.remove(_LOCK)
    except OSError:
        pass


def _cerrojo():
    try:
        os.makedirs(config.APP_DIR, exist_ok=True)
        actual = None
        if os.path.exists(_LOCK):
            try:
                actual = int(open(_LOCK).read().strip())
            except (OSError, ValueError):
                actual = None
        if actual is not None and _pid_vivo(actual):
            return False
        with open(_LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        atexit.register(_soltar_cerrojo)
        return True
    except OSError:
        return True


def _modo_updater(nuevo, destino, pid_orig):
    tope = time.time() + 30
    while time.time() < tope:
        if not _pid_vivo(pid_orig):
            break
        time.sleep(0.5)
    try:
        exe = updates.aplicar_upgrade(nuevo, destino)
    except Exception:
        return 3
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([exe], creationflags=flags, close_fds=True)
    return 0


if __name__ == "__main__":
    if "--aplicar-update" in sys.argv:
        i = sys.argv.index("--aplicar-update")
        nuevo, destino = sys.argv[i + 1], sys.argv[i + 2]
        pid = int(sys.argv[i + 3]) if len(sys.argv) > i + 3 else 0
        raise SystemExit(_modo_updater(nuevo, destino, pid))
    if not _cerrojo():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(TITULO, "Ya hay una instancia del Instalador de Mods abierta.")
        root.destroy()
    else:
        App().mainloop()