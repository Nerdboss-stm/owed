"""OWED agent. planner/gate/verifier are pure; drafter calls the actor model; executor is the only
module that performs a live write; asserter reads end state back."""
