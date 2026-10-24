"""Waiting for the wake word, the island gets the level of a voice above the room, not the room's noise."""

from jarvis.voice import VoiceController


def test_steady_room_is_silent_and_a_voice_shows():
    vc = VoiceController.__new__(VoiceController)
    vc._room_noise = None
    room = [vc._above_room(0.053 + (i % 5) * 0.002) for i in range(200)]
    assert max(room) == 0.0
    voice = [vc._above_room(0.25) for _ in range(10)]
    assert min(voice) > 0.1
    assert vc._room_noise < 0.07          # speech barely moves the floor
