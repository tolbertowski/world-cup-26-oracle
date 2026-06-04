"""Streamlit entrypoint for the World Cup Oracle app."""

from __future__ import annotations


def main() -> None:
    try:
        import streamlit as st
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised outside tests
        raise RuntimeError(
            "Streamlit is required to run the app. Install project dependencies first."
        ) from exc

    st.set_page_config(
        page_title="World Cup 26 Oracle",
        page_icon="WC",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title("World Cup 26 Oracle")
    st.caption("Predict match odds, tournament chaos, goals, cards, and corners.")
    st.info("Project scaffold ready. Tournament logic and models are added in later commits.")
