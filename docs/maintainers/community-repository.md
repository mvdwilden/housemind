# Joining the community App repository (E-136)

HouseMind is first offered from its own repository ("Add to Home Assistant" on the website).
After a Stable release it is proposed for the big community App repository. Not before every
item below is true – and only after the maintainer's explicit go.

## Checklist

- [ ] A Stable release passed the release test (E-144): automatic tests, S-01 to S-04 in every
      demo house, speed norms, tested Home Assistant versions, help matches the app, and two
      weeks on Beta in real houses without serious faults.
- [ ] Signed images for amd64 and aarch64 on GitHub (E-137, E-143); `image:` set in
      `ha_app_stable/config.yaml`; the owner is `mvdwilden` everywhere (B-43).
- [ ] Name and logo checked (E-166); icon.png and logo.png in the App folder.
- [ ] Privacy statement and disclaimer published (E-163, E-165).
- [ ] App documentation (DOCS.md) and changelog readable for users; website live in four
      languages (E-146, E-149).
- [ ] No open ports, no host network, no full access, AppArmor on (E-86).
- [ ] Knowledge bundle key generated (`tools/sign_bundle.py keygen`), public key in
      `housemind/knowledge/updates.py`, private key kept offline; INDEX_URL set (E-154).
- [ ] The opt-in count has an address and a short page that says what is counted (E-145).
- [ ] SECURITY.md with a reaction time; issue and discussion templates active (E-143, E-147).
- [ ] Donation links filled in `.github/FUNDING.yml` (E-138, E-139).
