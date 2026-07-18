"""Punto de entrada para PyInstaller.

`audioviz/visualizer.py` usa imports relativos (`from . import config`), asi que
NO puede ejecutarse como script suelto: como `__main__` no tiene paquete padre y
revienta con "attempted relative import with no known parent package". Este
launcher importa el paquete de forma normal y delega en su `main()`, de modo que
los imports relativos resuelven y PyInstaller sigue el arbol completo desde aca.
"""

from audioviz.visualizer import main

if __name__ == "__main__":
    main()
