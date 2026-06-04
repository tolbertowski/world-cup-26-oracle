"""Small in-repo data set used for demos, tests, and portfolio screenshots.

The 2026 finals field and fixtures should be imported from official snapshots
before production use. This module deliberately keeps the app runnable without
network access or paid APIs.
"""

from __future__ import annotations

from itertools import combinations

from world_cup_oracle.domain import Fixture, MatchStage, Team


DEMO_GROUPS: dict[str, list[tuple[str, str, str, int, float]]] = {
    "A": [
        ("MEX", "Mexico", "CONCACAF", 14, 1760),
        ("RSA", "South Africa", "CAF", 58, 1580),
        ("KOR", "South Korea", "AFC", 23, 1715),
        ("CZE", "Czech Republic", "UEFA", 32, 1685),
    ],
    "B": [
        ("CAN", "Canada", "CONCACAF", 35, 1665),
        ("BIH", "Bosnia and Herzegovina", "UEFA", 44, 1615),
        ("QAT", "Qatar", "AFC", 50, 1605),
        ("SUI", "Switzerland", "UEFA", 18, 1740),
    ],
    "C": [
        ("BRA", "Brazil", "CONMEBOL", 2, 1910),
        ("MAR", "Morocco", "CAF", 12, 1790),
        ("HAI", "Haiti", "CONCACAF", 79, 1495),
        ("SCO", "Scotland", "UEFA", 41, 1630),
    ],
    "D": [
        ("USA", "United States", "CONCACAF", 11, 1800),
        ("PAR", "Paraguay", "CONMEBOL", 38, 1645),
        ("AUS", "Australia", "AFC", 26, 1700),
        ("TUR", "Turkey", "UEFA", 27, 1705),
    ],
    "E": [
        ("GER", "Germany", "UEFA", 6, 1845),
        ("CUW", "Curacao", "CONCACAF", 82, 1485),
        ("CIV", "Ivory Coast", "CAF", 39, 1640),
        ("ECU", "Ecuador", "CONMEBOL", 25, 1708),
    ],
    "F": [
        ("NED", "Netherlands", "UEFA", 7, 1840),
        ("JPN", "Japan", "AFC", 17, 1750),
        ("SWE", "Sweden", "UEFA", 30, 1690),
        ("TUN", "Tunisia", "CAF", 46, 1610),
    ],
    "G": [
        ("BEL", "Belgium", "UEFA", 8, 1830),
        ("EGY", "Egypt", "CAF", 34, 1668),
        ("IRN", "Iran", "AFC", 21, 1720),
        ("NZL", "New Zealand", "OFC", 91, 1460),
    ],
    "H": [
        ("ESP", "Spain", "UEFA", 3, 1895),
        ("CPV", "Cape Verde", "CAF", 56, 1585),
        ("KSA", "Saudi Arabia", "AFC", 54, 1590),
        ("URU", "Uruguay", "CONMEBOL", 13, 1785),
    ],
    "I": [
        ("ARG", "Argentina", "CONMEBOL", 1, 1930),
        ("ENG", "England", "UEFA", 4, 1885),
        ("NOR", "Norway", "UEFA", 31, 1688),
        ("PAN", "Panama", "CONCACAF", 49, 1608),
    ],
    "J": [
        ("FRA", "France", "UEFA", 5, 1875),
        ("SEN", "Senegal", "CAF", 20, 1725),
        ("CRC", "Costa Rica", "CONCACAF", 47, 1612),
        ("UAE", "United Arab Emirates", "AFC", 67, 1535),
    ],
    "K": [
        ("POR", "Portugal", "UEFA", 9, 1825),
        ("CRO", "Croatia", "UEFA", 16, 1755),
        ("JAM", "Jamaica", "CONCACAF", 55, 1588),
        ("UZB", "Uzbekistan", "AFC", 52, 1596),
    ],
    "L": [
        ("COL", "Colombia", "CONMEBOL", 10, 1810),
        ("NGR", "Nigeria", "CAF", 36, 1660),
        ("UKR", "Ukraine", "UEFA", 28, 1698),
        ("CHN", "China", "AFC", 88, 1470),
    ],
}


def build_demo_teams() -> list[Team]:
    teams: list[Team] = []
    for group, entries in DEMO_GROUPS.items():
        for code, name, confederation, fifa_rank, seed_rating in entries:
            teams.append(
                Team(
                    code=code,
                    name=name,
                    group=group,
                    confederation=confederation,
                    fifa_rank=fifa_rank,
                    seed_rating=seed_rating,
                )
            )
    return teams


def build_demo_fixtures() -> list[Fixture]:
    fixtures: list[Fixture] = []
    match_number = 1
    for group, entries in DEMO_GROUPS.items():
        codes = [entry[0] for entry in entries]
        for home, away in combinations(codes, 2):
            fixtures.append(
                Fixture(
                    match_id=f"G{match_number:03d}",
                    stage=MatchStage.GROUP,
                    home_team=home,
                    away_team=away,
                    group=group,
                )
            )
            match_number += 1
    return fixtures
