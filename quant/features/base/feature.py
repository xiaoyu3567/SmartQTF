from abc import ABC, abstractmethod


class Feature(ABC):
    @abstractmethod
    def compute(self, data):
        pass
