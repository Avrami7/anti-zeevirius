rule Suspicious_Double_Extension
{
    meta:
        description = "Fichier avec double extension déguisant un exécutable (ex: facture.pdf.exe)"
        severity = "medium"
    strings:
        $ext1 = ".pdf.exe" nocase
        $ext2 = ".docx.exe" nocase
        $ext3 = ".jpg.exe" nocase
        $ext4 = ".xlsx.exe" nocase
    condition:
        any of them
}

rule Suspicious_PowerShell_Encoded_Command
{
    meta:
        description = "Commande PowerShell encodée en base64 (technique d'évasion courante)"
        severity = "high"
    strings:
        $a = "-enc " nocase
        $b = "-EncodedCommand" nocase
        $c = "FromBase64String" nocase
    condition:
        any of them
}

rule Suspicious_Macro_AutoExec
{
    meta:
        description = "Macro Office avec exécution automatique (vecteur d'infection classique)"
        severity = "medium"
    strings:
        $a = "AutoOpen" nocase
        $b = "Document_Open" nocase
        $c = "Shell(" nocase
        $d = "WScript.Shell" nocase
    condition:
        2 of them
}

rule Suspicious_Reverse_Shell_Strings
{
    meta:
        description = "Chaînes typiques d'un reverse shell"
        severity = "critical"
    strings:
        $a = "cmd.exe /c" nocase
        $b = "/bin/sh -i" nocase
        $c = "socket.connect" nocase
    condition:
        any of them
}