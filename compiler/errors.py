class CompilerError(ValueError):
    def __init__(self, phase, message, line=None, column=None):
        super().__init__(message)
        self.phase, self.line, self.column = phase, line, column

    def detail(self):
        return {"phase": self.phase, "message": str(self),
                "line": self.line, "column": self.column}
