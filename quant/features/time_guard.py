class TimeGuard:
    @staticmethod
    def enforce(data, current_index):
        """
        Only return data with index <= current_index.
        """
        return data[: current_index + 1]
