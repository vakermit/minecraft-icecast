-- mcradio CC:Tweaked Installer
-- Downloads radio client files from the mcradio server.
--
-- Usage:
--   wget run http://127.0.0.1:5309/client/installer.lua
--   wget run http://127.0.0.1:5309/client/installer.lua http://192.168.1.100:5309

local args = { ... }
local SERVER = args[1] or "http://127.0.0.1:5309"

-- Files to download
local files = {
    "radio.lua",
}

print("")
print("  " .. string.char(14) .. " mcradio installer " .. string.char(14))
print("")
print("  Server: " .. SERVER)
print("")

local installed = 0
local failed = 0

for _, filename in ipairs(files) do
    local url = SERVER .. "/client/" .. filename
    write("  Downloading " .. filename .. "... ")

    local response = http.get(url)
    if response then
        local content = response.readAll()
        response.close()

        local file = fs.open(filename, "w")
        file.write(content)
        file.close()

        print("OK")
        installed = installed + 1
    else
        print("FAILED")
        failed = failed + 1
    end
end

print("")
if failed == 0 then
    print("  Installed " .. installed .. " file(s).")
    print("")
    print("  Run the radio with:")
    print("    radio")
    print("")
else
    print("  Installed: " .. installed .. ", Failed: " .. failed)
    print("  Check that the server is running at " .. SERVER)
end
