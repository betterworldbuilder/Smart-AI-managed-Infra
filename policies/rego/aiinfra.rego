# ---------------------------------------------------------------------------
# Rego mirror of the deterministic policy rules (section 12).
#
# With POLICY_ENGINE=opa the platform POSTs
#   {"input": {"intent": ..., "inventory": ..., "candidates": {...}}}
# to  /v1/data/aiinfra/authz  and merges the returned `decisions` on top of the
# built-in Python rules. The Python engine always runs first, so OPA being
# unavailable degrades the governance story gracefully instead of breaking it.
#
# Each decision is shaped like the platform's PolicyDecision model:
#   rule_id, title, effect (deny|require|prefer|discourage|warn|info),
#   message, runtime (optional), score_delta (optional), obligations (optional)
# ---------------------------------------------------------------------------

package aiinfra.authz

import rego.v1

default allow := true

intent := input.intent

candidates := object.keys(input.candidates)

is_vm(runtime) if {
	startswith(runtime, "OPENSTACK")
}

is_container(runtime) if {
	not is_vm(runtime)
}

uses_gpu(runtime) if {
	contains(runtime, "GPU")
}

# --- RULE 1: restricted data never leaves the private cloud ----------------
decisions contains decision if {
	intent.sensitive_data == "restricted"
	decision := {
		"rule_id": "OPA-001",
		"title": "Restricted data stays on-premises",
		"effect": "require",
		"message": "OPA: restricted classification forbids public cloud, requires encryption at rest and internal-only networking.",
		"obligations": {
			"public_cloud_allowed": false,
			"encryption_at_rest": true,
			"network_exposure": "internal",
			"network_policy": true,
		},
	}
}

# --- RULE 2: never allocate a GPU that was not requested -------------------
decisions contains decision if {
	not intent.gpu_required
	some runtime in candidates
	uses_gpu(runtime)
	decision := {
		"rule_id": "OPA-002",
		"title": "GPU not required",
		"effect": "deny",
		"runtime": runtime,
		"message": "OPA: the workload does not require a GPU.",
	}
}

# --- RULE 3: development takes the cheapest compatible GPU -----------------
decisions contains decision if {
	intent.environment == "dev"
	intent.gpu_required
	decision := {
		"rule_id": "OPA-003",
		"title": "Development prefers the lowest-cost GPU",
		"effect": "info",
		"message": "OPA: development workloads should not hold premium accelerators.",
	}
}

# --- RULE 4: strict isolation prefers a VM ---------------------------------
decisions contains decision if {
	intent.workload_isolation == "high"
	some runtime in candidates
	is_container(runtime)
	decision := {
		"rule_id": "OPA-004",
		"title": "High isolation discourages shared kernels",
		"effect": "discourage",
		"runtime": runtime,
		"score_delta": -5,
		"message": "OPA: containers share the host kernel; strict isolation was requested.",
	}
}

# --- RULE 5: container-friendly AI inference belongs on Kubernetes GPU -----
decisions contains decision if {
	intent.workload_type in {"llm-inference", "ai-inference", "ml-inference"}
	intent.container_compatible != false
	intent.workload_isolation != "high"
	"KUBERNETES_GPU" in candidates
	decision := {
		"rule_id": "OPA-005",
		"title": "Container-compatible AI inference",
		"effect": "prefer",
		"runtime": "KUBERNETES_GPU",
		"score_delta": 1,
		"message": "OPA: container-compatible inference with no strict isolation requirement.",
	}
}

# --- Guard rail: classified data must not be publicly exposed --------------
decisions contains decision if {
	intent.sensitive_data in {"confidential", "restricted"}
	intent.network_exposure == "public"
	decision := {
		"rule_id": "OPA-015",
		"title": "Public exposure denied for classified data",
		"effect": "deny",
		"message": "OPA: classification forbids public exposure.",
	}
}

# --- Guard rail: production databases should be highly available -----------
decisions contains decision if {
	intent.workload_type in {"database", "postgresql", "postgres"}
	intent.environment == "prod"
	not intent.high_availability
	decision := {
		"rule_id": "OPA-010",
		"title": "Production database without HA",
		"effect": "warn",
		"message": "OPA: a production database without high availability is a single point of failure.",
	}
}

# Convenience: a single boolean for callers that only want allow/deny.
deny_count := count([d | some d in decisions; d.effect == "deny"])

allow := false if {
	deny_count > 0
}
