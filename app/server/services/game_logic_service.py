from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Literal

from fastapi import HTTPException, status


PlayerRole = Literal["Nurse", "Driver", "Guard", "Student", "Vendor"]


@dataclass(slots=True)
class PlayerState:
    player_id: str
    role: PlayerRole
    vulnerability_score: float
    inventory: list[str]
    coins: int
    checklist: list[str]
    is_infected: bool = False


@dataclass(slots=True)
class TradeResult:
    from_player_id: str
    to_player_id: str
    sent_item_id: str
    received_item_id: str
    transmission_occurred: bool


class GameManager:
    REQUIRED_PLAYERS: int = 10
    STARTING_COINS: int = 100
    CHECKLIST_SIZE: int = 5
    STARTING_INVENTORY_SIZE: int = 3

    ROLE_VULNERABILITY: dict[PlayerRole, float] = {
        "Nurse": 0.35,
        "Driver": 0.30,
        "Guard": 0.25,
        "Student": 0.40,
        "Vendor": 0.45,
    }

    ITEM_POOL: tuple[str, ...] = (
        "Mask",
        "Vitamin-C",
        "Alcohol",
        "Gloves",
        "Notebook",
        "Bus-Pass",
        "Meal-Ticket",
        "Thermometer",
        "Water-Bottle",
        "ID-Lanyard",
        "Face-Shield",
        "Sanitizer",
        "Bandage",
        "Pen",
        "Clinic-Stub",
    )

    REAL_SYMPTOMS: tuple[str, ...] = (
        "fever",
        "dry cough",
        "fatigue",
        "headache",
        "sore throat",
        "body aches",
    )

    FALSE_POSITIVE_SYMPTOMS: tuple[str, ...] = (
        "allergy sneezing",
        "mild stress headache",
        "sleep-related fatigue",
        "voice strain",
        "dehydration dizziness",
        "seasonal sniffles",
    )

    def __init__(self, lobby_id: str, seed: int | None = None, required_players: int | None = None) -> None:
        self.lobby_id = lobby_id
        self._rng = Random(seed)
        self._players: dict[str, PlayerState] = {}
        self._patient_zero_id: str | None = None
        self._initialized = False
        self.REQUIRED_PLAYERS = required_players if required_players is not None else self.__class__.REQUIRED_PLAYERS

    @property
    def players(self) -> dict[str, PlayerState]:
        return self._players

    def initialize_game(self, player_list: list[str]) -> dict[str, object]:
        if self._initialized:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Game lobby is already initialized.",
            )

        normalized_players = [player_id.strip() for player_id in player_list]
        if len(normalized_players) != self.REQUIRED_PLAYERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Exactly {self.REQUIRED_PLAYERS} players are required.",
            )

        if any(not player_id for player_id in normalized_players):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Player IDs must be non-empty strings.",
            )

        unique_ids = set(normalized_players)
        if len(unique_ids) != len(normalized_players):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate player IDs are not allowed.",
            )

        for player_id in normalized_players:
            role = self._rng.choice(list(self.ROLE_VULNERABILITY.keys()))
            vulnerability = self.ROLE_VULNERABILITY[role]
            checklist = self._rng.sample(list(self.ITEM_POOL), self.CHECKLIST_SIZE)
            inventory = self._rng.sample(list(self.ITEM_POOL), self.STARTING_INVENTORY_SIZE)
            self._players[player_id] = PlayerState(
                player_id=player_id,
                role=role,
                vulnerability_score=vulnerability,
                inventory=inventory,
                coins=self.STARTING_COINS,
                checklist=checklist,
                is_infected=False,
            )

        self._patient_zero_id = self._rng.choice(normalized_players)
        self._players[self._patient_zero_id].is_infected = True
        self._initialized = True

        public_players: list[dict[str, object]] = []
        for player in self._players.values():
            public_players.append(
                {
                    "player_id": player.player_id,
                    "role": player.role,
                    "vulnerability_score": player.vulnerability_score,
                    "inventory": list(player.inventory),
                    "coins": player.coins,
                    "checklist": list(player.checklist),
                }
            )

        return {
            "lobby_id": self.lobby_id,
            "player_count": len(self._players),
            "players": public_players,
            "patient_zero_assigned": True,
        }

    def process_trade(self, player_a_id: str, player_b_id: str, item_id: str) -> TradeResult:
        self._assert_initialized()

        player_a = self._get_player_or_raise(player_a_id)
        player_b = self._get_player_or_raise(player_b_id)

        if player_a.player_id == player_b.player_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A player cannot trade with themselves.",
            )

        if item_id not in player_a.inventory:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item '{item_id}' was not found in player '{player_a_id}' inventory.",
            )

        if not player_b.inventory:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Player '{player_b_id}' has no item to swap.",
            )

        received_item = self._rng.choice(player_b.inventory)

        player_a.inventory.remove(item_id)
        player_b.inventory.remove(received_item)
        player_a.inventory.append(received_item)
        player_b.inventory.append(item_id)

        transmission_occurred = False
        if player_a.is_infected and not player_b.is_infected:
            transmission_occurred = self._calculate_transmission(player_b, player_a)
        elif player_b.is_infected and not player_a.is_infected:
            transmission_occurred = self._calculate_transmission(player_a, player_b)

        return TradeResult(
            from_player_id=player_a_id,
            to_player_id=player_b_id,
            sent_item_id=item_id,
            received_item_id=received_item,
            transmission_occurred=transmission_occurred,
        )

    def _calculate_transmission(self, healthy_player: PlayerState, infected_player: PlayerState) -> bool:
        if healthy_player.is_infected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transmission check requires a healthy target player.",
            )

        if not infected_player.is_infected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transmission check requires an infected source player.",
            )

        infection_roll = self._rng.random()
        infected = infection_roll < healthy_player.vulnerability_score
        if infected:
            healthy_player.is_infected = True
        return infected

    def generate_activity_log(self) -> list[str]:
        self._assert_initialized()

        infected_players = [player for player in self._players.values() if player.is_infected]
        healthy_players = [player for player in self._players.values() if not player.is_infected]

        if not infected_players and not healthy_players:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cannot generate activity log without active players.",
            )

        logs: list[str] = []

        if infected_players:
            infected = self._rng.choice(infected_players)
            symptom = self._rng.choice(list(self.REAL_SYMPTOMS))
            logs.append(f"Player {infected.player_id} reported {symptom}.")

        if healthy_players:
            healthy = self._rng.choice(healthy_players)
            false_symptom = self._rng.choice(list(self.FALSE_POSITIVE_SYMPTOMS))
            logs.append(f"Player {healthy.player_id} reported {false_symptom}.")

        all_players = list(self._players.values())
        while len(logs) < 3:
            selected = self._rng.choice(all_players)
            if selected.is_infected:
                symptom = self._rng.choice(list(self.REAL_SYMPTOMS))
                logs.append(f"Player {selected.player_id} reported {symptom}.")
            else:
                false_symptom = self._rng.choice(list(self.FALSE_POSITIVE_SYMPTOMS))
                logs.append(f"Player {selected.player_id} reported {false_symptom}.")

        self._rng.shuffle(logs)
        return logs[:3]

    def _assert_initialized(self) -> None:
        if not self._initialized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Game lobby has not been initialized.",
            )

    def _get_player_or_raise(self, player_id: str) -> PlayerState:
        player = self._players.get(player_id)
        if player is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Player '{player_id}' not found in this lobby.",
            )
        return player
