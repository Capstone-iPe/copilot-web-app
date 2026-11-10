"""Talks to the iPe onboarding service, where reviews for iPe staff live.

The real onboarding service doesn't exist yet in this project, so this stand-in records each
review in the chat service's own database. When the real service is ready, replace the two
functions below with HTTP calls to it; nothing else has to change.

Only the chat service calls this. The website never does (web app requirements, last section).
"""


class OnboardingClient:
    def __init__(self, store):
        self.store = store

    def open_review(self, conversation_id, args):
        self.store.upsert_review(args["review_id"], conversation_id, args.get("shop"), args.get("assignee"),
                                 args.get("volume_inr"), args.get("missing", []))

    def update_review(self, conversation_id, args):
        r = self.store.review(args["review_id"])
        if r:
            self.store.upsert_review(r["review_id"], conversation_id, r["shop"], r["assignee"], r["volume_inr"],
                                     args.get("missing", []))

    def handle(self, conversation_id, actions):
        """Carry out the actions the agent asked for. Anything not on this list is ignored."""
        for a in actions or []:
            name = a.get("name") if isinstance(a, dict) else a
            if name == "open_review":
                self.open_review(conversation_id, a["args"])
            elif name == "update_review":
                self.update_review(conversation_id, a["args"])
            # activate_merchant and similar are deliberately not supported: the copilot can't turn a shop on.
