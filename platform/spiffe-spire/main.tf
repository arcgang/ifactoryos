# spiffe-spire — SPIFFE/SPIRE, X.509 SVIDs, mTLS
# LLD component cmp:spiffe-spire. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}
