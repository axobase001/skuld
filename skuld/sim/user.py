"""S5 scripted user: says the world's intents (through a paraphraser that sees only the intent text)
and judges deliveries by the hidden list, deterministically."""
from skuld.sim import score


class ScriptedUser:
    def __init__(self, world, paraphrase=None):
        self.world, self.paraphrase = world, paraphrase or (lambda intent: intent)

    def utterances(self, from_h, to_h):
        """[(at_h, text)] for the intents in [from_h, to_h), in order."""
        return [(i["at_h"], self.paraphrase(i["intent"])) for i in self.world.intents if from_h <= i["at_h"] < to_h]

    def feedback(self, delivery):
        return "useful" if score.useful(self.world.items, delivery) else "useless"


__all__ = ["ScriptedUser"]
