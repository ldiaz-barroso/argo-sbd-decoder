"""
argofluxdecoder
===============
Pure-Python decoder for Argo float Iridium SBD files.

Translated from the Coriolis MATLAB data processing chain:
  https://github.com/euroargodev/Coriolis-data-processing-chain-for-Argo-floats
  DOI: https://doi.org/10.17882/45589

Supports NKE ARVOR / PROVOR families (decoder IDs 201-232).
Architecture is modular: new float types can be added under floats/.
"""

__version__ = "1.0.0"
__author__ = "SOCIB"
__credits__ = [
    "Jean-Philippe Rannou (Capgemini / Ifremer) — original MATLAB decoder",
    "SOCIB — Python translation and integration",
]
