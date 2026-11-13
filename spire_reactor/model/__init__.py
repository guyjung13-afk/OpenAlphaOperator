"""Local model helpers. The desk talks only to an Ollama server on this machine."""

from spire_reactor.model.ollama_desk import ask_desk, probe_ollama

__all__ = ["ask_desk", "probe_ollama"]
