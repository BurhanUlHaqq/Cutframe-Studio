class RenderCancelled(RuntimeError):
    """Unwind a render at a worker checkpoint after a cancellation request."""
