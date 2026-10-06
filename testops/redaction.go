package testrunner

import "regexp"

// SecretKeyPattern matches map keys whose values are likely secrets.
// Used by run-bundle / report writers to redact sensitive values
// before they hit disk. The pattern is case-insensitive and matches
// substrings, not whole keys: e.g. "chap_secret", "api_token",
// "user_password", "private_key" all match.
//
// Adding a key here is safer than missing one — false positives
// just produce a redacted artifact, false negatives leak credentials.
var SecretKeyPattern = regexp.MustCompile(`(?i)(secret|password|token|chap|key|credential)`)

// RedactedValue is the constant placeholder substituted for any
// matched value. Field presence is preserved (not removed) so the
// bundle still records that the field existed.
const RedactedValue = "***"

// RedactMap returns a copy of m with values for any secret-named
// key replaced by RedactedValue. The input is not modified.
//
// nil input returns nil (callers can pass through map fields without
// allocating empty maps).
func RedactMap(m map[string]string) map[string]string {
	if m == nil {
		return nil
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		if SecretKeyPattern.MatchString(k) {
			out[k] = RedactedValue
		} else {
			out[k] = v
		}
	}
	return out
}

// RedactAction returns a copy of an Action with secret-named Params
// values redacted. Used by the engine when storing the rendered
// action YAML in result.json so the bundle never contains live
// credential values inline.
func RedactAction(a Action) Action {
	a.Params = RedactMap(a.Params)
	return a
}
