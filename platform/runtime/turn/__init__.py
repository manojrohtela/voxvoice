"""runtime.turn — Turn Intelligence (Constitution §A4.2, ADR-0005).

Speech boundaries: activity detection, endpoint scoring (adaptive + semantic),
interruption policy (min-words, backchannel tolerance, confidence gates), and
response-timing hints. Boundary only at M1; implemented at M3.
"""
