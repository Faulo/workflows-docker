import json

from registry import content_digest


class MemoryRegistry:
    def __init__(self, repository):
        self.repository = repository
        self.documents, self.configs, self.tags, self.payloads = {}, {}, {}, {}
        self.reads = []
        self.reads_before_first_put = None

    def manifest(self, reference):
        self.reads.append(reference)
        if "@" in reference:
            return self.documents[reference.split("@")[1]]
        return self.tags[reference.split(":")[1]]

    def config(self, reference):
        self.reads.append(reference + "/config")
        return self.configs[reference.split("@")[1]]

    def put_manifest(self, tag, payload, media_type):
        if self.reads_before_first_put is None:
            self.reads_before_first_put = self.reads.copy()
        document = json.loads(payload)
        assert document["mediaType"] == media_type
        self.tags[tag] = {**document, "digest": content_digest(payload)}
        self.payloads[tag] = payload
