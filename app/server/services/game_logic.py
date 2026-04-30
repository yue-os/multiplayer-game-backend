from __future__ import annotations

from random import Random
from typing import Mapping

from fastapi import HTTPException, status

from app.server.models.game_models import GameState, HealthStatus, ItemType, LocationEvent, PlayerState


class GameEngine:
    def __init__(self, game_state: GameState, seed: int | None = None) -> None:
        self.game_state = game_state
        self._rng = Random(seed)

    def process_trade(
        self,
        player_a: PlayerState,
        player_b: PlayerState,
        items_offered_a: Mapping[ItemType, int],
        items_offered_b: Mapping[ItemType, int],
    ) -> dict[str, object]:
        if player_a.player_id == player_b.player_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A player cannot trade with themselves.",
            )

        if not items_offered_a and not items_offered_b:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Trade requires at least one offered item.",
            )

        self._validate_offer_counts(items_offered_a, "items_offered_a")
        self._validate_offer_counts(items_offered_b, "items_offered_b")

        self._assert_player_has_items(player_a, items_offered_a)
        self._assert_player_has_items(player_b, items_offered_b)

        self._remove_items(player_a, items_offered_a)
        self._remove_items(player_b, items_offered_b)

        self._add_items(player_a, items_offered_b)
        self._add_items(player_b, items_offered_a)

        self._calculate_infection_risk(player_a, player_b, self.game_state.current_event)

        return {
            "from_player_id": player_a.player_id,
            "to_player_id": player_b.player_id,
            "items_from_a": {item.value: count for item, count in items_offered_a.items()},
            "items_from_b": {item.value: count for item, count in items_offered_b.items()},
            "health_after_trade": {
                player_a.player_id: player_a.health_status.value,
                player_b.player_id: player_b.health_status.value,
            },
        }

    def _calculate_infection_risk(
        self,
        player_a: PlayerState,
        player_b: PlayerState,
        current_event: LocationEvent,
    ) -> None:
        a_contagious = player_a.is_carrier or player_a.health_status == HealthStatus.INFECTED
        b_contagious = player_b.is_carrier or player_b.health_status == HealthStatus.INFECTED

        if a_contagious == b_contagious:
            return

        source = player_a if a_contagious else player_b
        target = player_b if a_contagious else player_a

        if target.health_status == HealthStatus.INFECTED:
            return

        infection_risk = 0.25

        if current_event == LocationEvent.CANTEEN:
            infection_risk += 0.20

        mask_count_target = int(target.inventory.get(ItemType.MASKS, 0))
        if mask_count_target > 0:
            infection_risk -= min(0.20, 0.10 * mask_count_target)

        mask_count_source = int(source.inventory.get(ItemType.MASKS, 0))
        if mask_count_source > 0:
            infection_risk -= min(0.10, 0.05 * mask_count_source)

        infection_risk = max(0.0, min(1.0, infection_risk))

        if self._rng.random() > infection_risk:
            return

        if target.health_status == HealthStatus.HEALTHY:
            target.health_status = HealthStatus.EXPOSED
        elif target.health_status == HealthStatus.EXPOSED:
            target.health_status = HealthStatus.INFECTED

    def compute_scores(self) -> list[dict[str, object]]:
        """Return players ranked by score, highest first.

        Scoring rules:
          +500  mission completed
          +10   per item in inventory (rewards active traders)
          -200  if infected (health penalty)
        """
        results: list[dict[str, object]] = []
        for player in self.game_state.players:
            score = 0
            if player.mission_completed:
                score += 500
            score += sum(player.inventory.values()) * 10
            if player.health_status == HealthStatus.INFECTED:
                score -= 200
            results.append({
                "player_id": player.player_id,
                "display_name": player.player_id,
                "score": score,
                "mission_completed": player.mission_completed,
                "health_status": player.health_status.value,
            })
        results.sort(key=lambda p: p["score"], reverse=True)
        return results

    def rotate_event(self) -> str:
        available_events = list(LocationEvent)
        if len(available_events) <= 1:
            selected_event = self.game_state.current_event
        else:
            selectable_events = [evt for evt in available_events if evt != self.game_state.current_event]
            selected_event = self._rng.choice(selectable_events)

        self.game_state.current_event = selected_event

        event_rules: dict[LocationEvent, str] = {
            LocationEvent.SCHOOL: "Structured exchanges only: each trade can include up to 2 item stacks.",
            LocationEvent.PARK: "Open-air safety bonus: infection risk is slightly reduced this round.",
            LocationEvent.CANTEEN: "Crowded hotspot: infection risk is higher for all close-contact trades.",
            LocationEvent.CLINIC: "Medical oversight: medicine trades grant better recovery opportunities.",
            LocationEvent.MARKET: "High-volume trading: players may trade with more flexibility this round.",
        }

        return (
            f"Round event: {selected_event.value}. "
            f"Rule update: {event_rules[selected_event]}"
        )

    def _validate_offer_counts(self, offered_items: Mapping[ItemType, int], field_name: str) -> None:
        for item_type, count in offered_items.items():
            if not isinstance(item_type, ItemType):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field_name} contains an invalid item type.",
                )
            if count <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field_name} has a non-positive quantity for {item_type.value}.",
                )

    def _assert_player_has_items(self, player: PlayerState, offered_items: Mapping[ItemType, int]) -> None:
        for item_type, count in offered_items.items():
            current_count = int(player.inventory.get(item_type, 0))
            if current_count < count:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Player '{player.player_id}' does not have enough {item_type.value}. "
                        f"Required {count}, available {current_count}."
                    ),
                )

    def _remove_items(self, player: PlayerState, offered_items: Mapping[ItemType, int]) -> None:
        for item_type, count in offered_items.items():
            updated_count = int(player.inventory.get(item_type, 0)) - count
            if updated_count <= 0:
                player.inventory.pop(item_type, None)
            else:
                player.inventory[item_type] = updated_count

    def _add_items(self, player: PlayerState, offered_items: Mapping[ItemType, int]) -> None:
        for item_type, count in offered_items.items():
            player.inventory[item_type] = int(player.inventory.get(item_type, 0)) + count
