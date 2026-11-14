package testrunner

import "testing"

func TestRedactMap(t *testing.T) {
	in := map[string]string{
		"host":             "example.com",
		"password":         "p@ss",
		"PASSWORD":         "PWD",
		"chap_secret":      "shh",
		"api_token":        "Bearer ...",
		"private_key":      "-----BEGIN-----",
		"ssh_KEY_path":     "/etc/key",
		"plain":            "ok",
		"credential_blob":  "x",
	}
	got := RedactMap(in)

	for _, k := range []string{"password", "PASSWORD", "chap_secret", "api_token", "private_key", "ssh_KEY_path", "credential_blob"} {
		if got[k] != RedactedValue {
			t.Errorf("%s = %q, want %q", k, got[k], RedactedValue)
		}
	}
	for _, k := range []string{"host", "plain"} {
		if got[k] == RedactedValue {
			t.Errorf("%s redacted but should be passthrough; got %q", k, got[k])
		}
	}
	if got["host"] != "example.com" || got["plain"] != "ok" {
		t.Errorf("non-secret values mangled: host=%q plain=%q", got["host"], got["plain"])
	}
	// Original is untouched.
	if in["password"] != "p@ss" {
		t.Errorf("RedactMap mutated input: in[password]=%q", in["password"])
	}
}

func TestRedactMap_NilPassthrough(t *testing.T) {
	if got := RedactMap(nil); got != nil {
		t.Errorf("RedactMap(nil) = %v, want nil", got)
	}
}

func TestRedactAction(t *testing.T) {
	a := Action{
		Action: "iscsi_login",
		Params: map[string]string{
			"target":        "primary",
			"chap_secret":   "shhhh",
			"chap_username": "x",
			"node":          "m02",
		},
	}
	r := RedactAction(a)
	if r.Action != "iscsi_login" {
		t.Errorf("Action name lost: %q", r.Action)
	}
	if r.Params["chap_secret"] != RedactedValue {
		t.Errorf("chap_secret not redacted: %q", r.Params["chap_secret"])
	}
	if r.Params["target"] != "primary" {
		t.Errorf("non-secret target mangled: %q", r.Params["target"])
	}
	// Original untouched.
	if a.Params["chap_secret"] != "shhhh" {
		t.Errorf("RedactAction mutated input")
	}
}
