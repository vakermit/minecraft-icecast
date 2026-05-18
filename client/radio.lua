-- Minecraft Radio Client for CC:Tweaked
-- Phase 1: Single station, DFPWM playback, retro UI

local SERVER = "http://127.0.0.1:5309"
local POLL_INTERVAL = 5
local CHUNK_SIZE = 16384

-- State
local playing = false
local station = nil
local metadata = {}
local volume = 1.0
local buffer = {}
local bufferSize = 0
local decoder = nil
local speaker = nil
local status = "Starting..."

-- Colors
local C = {
    bg = colors.black,
    header = colors.yellow,
    station = colors.white,
    meta = colors.lightGray,
    controls = colors.cyan,
    error = colors.red,
    bar = colors.lime,
    barBg = colors.gray,
}

local function findSpeaker()
    speaker = peripheral.find("speaker")
    return speaker ~= nil
end

local function drawUI()
    local w, h = term.getSize()
    term.setBackgroundColor(C.bg)
    term.clear()

    -- Header
    term.setCursorPos(1, 1)
    term.setTextColor(C.header)
    local freq = station and station.frequency or "---"
    local header = string.char(14) .. " RADIO " .. string.char(14)
    local freqStr = "[" .. freq .. " FM]"
    term.write(header)
    term.setCursorPos(w - #freqStr + 1, 1)
    term.write(freqStr)

    -- Divider
    term.setCursorPos(1, 2)
    term.setTextColor(C.controls)
    term.write(string.rep(string.char(140), w))

    -- Station name
    term.setCursorPos(1, 4)
    term.setTextColor(C.station)
    if station then
        term.write("  " .. string.char(16) .. " " .. station.name)
    else
        term.write("  No station")
    end

    -- Now playing
    term.setCursorPos(1, 6)
    term.setTextColor(C.meta)
    if metadata.title then
        term.write("    Now Playing:")
        term.setCursorPos(1, 7)
        term.write("    \"" .. metadata.title .. "\"")
        if metadata.artist then
            term.setCursorPos(1, 8)
            term.write("     - " .. metadata.artist)
        end
    else
        term.write("    " .. status)
    end

    -- Volume bar
    term.setCursorPos(1, h - 3)
    term.setTextColor(C.station)
    local volPct = math.floor(volume / 3.0 * 100)
    local barWidth = 10
    local filled = math.floor(volume / 3.0 * barWidth)
    term.write("  Vol: ")
    term.setTextColor(C.bar)
    term.write(string.rep(string.char(127), filled))
    term.setTextColor(C.barBg)
    term.write(string.rep(string.char(127), barWidth - filled))
    term.setTextColor(C.meta)
    term.write(" " .. volPct .. "%")

    -- Divider
    term.setCursorPos(1, h - 1)
    term.setTextColor(C.controls)
    term.write(string.rep(string.char(140), w))

    -- Controls
    term.setCursorPos(1, h)
    term.setTextColor(C.controls)
    term.write("  [")
    term.setTextColor(C.station)
    term.write(string.char(17) .. "/" .. string.char(16))
    term.setTextColor(C.controls)
    term.write("] Volume  [")
    term.setTextColor(C.station)
    term.write("Q")
    term.setTextColor(C.controls)
    term.write("] Quit")
end

local function fetchStations()
    local response = http.get(SERVER .. "/stations", nil, false)
    if not response then return nil end
    local body = response.readAll()
    response.close()
    local data = textutils.unserialiseJSON(body)
    if data and data.stations and #data.stations > 0 then
        return data.stations[1]
    end
    return nil
end

local function fetchChunk()
    if not station then return nil end
    local url = SERVER .. "/stream/" .. station.id
    local response = http.get(url, nil, true)
    if not response then return nil end
    local code = response.getResponseCode()
    if code == 200 then
        local data = response.readAll()
        response.close()
        return data
    end
    response.close()
    return nil
end

local function fetchMetadata()
    if not station then return end
    local response = http.get(SERVER .. "/now-playing/" .. station.id, nil, false)
    if not response then return end
    local body = response.readAll()
    response.close()
    local data = textutils.unserialiseJSON(body)
    if data then
        metadata = data
    end
end

local function audioLoop()
    decoder = require("cc.audio.dfpwm").make_decoder()

    -- Pre-fetch first chunk immediately
    status = "Tuning..."
    drawUI()

    local retries = 0
    while retries < 10 do
        local chunk = fetchChunk()
        if chunk then
            buffer[1] = chunk
            bufferSize = 1
            break
        end
        retries = retries + 1
        sleep(1)
    end

    if bufferSize == 0 then
        status = "No audio available"
        drawUI()
        return
    end

    playing = true
    while playing do
        local chunk = nil
        if bufferSize > 0 then
            chunk = buffer[1]
            buffer[1] = nil
            bufferSize = 0
        else
            chunk = fetchChunk()
        end

        if not chunk then
            status = "Tuning..."
            drawUI()
            sleep(1)
        else
            status = "Playing"
            local samples = decoder(chunk)
            local ok = pcall(function()
                while not speaker.playAudio(samples, volume) do
                    os.pullEvent("speaker_audio_empty")
                end
            end)

            if not ok then
                if not findSpeaker() then
                    status = "No speaker!"
                    drawUI()
                    while not findSpeaker() do
                        sleep(2)
                    end
                    decoder = require("cc.audio.dfpwm").make_decoder()
                end
            else
                -- Pre-fetch next chunk while speaker plays current
                local nextChunk = fetchChunk()
                if nextChunk then
                    buffer[1] = nextChunk
                    bufferSize = 1
                end
                -- Wait for speaker to need more audio
                os.pullEvent("speaker_audio_empty")
            end
        end
    end
end

local function metadataLoop()
    while true do
        fetchMetadata()
        drawUI()
        sleep(POLL_INTERVAL)
    end
end

local function inputLoop()
    while true do
        local event, key = os.pullEvent("key")
        if key == keys.q then
            playing = false
            term.setBackgroundColor(colors.black)
            term.setTextColor(colors.white)
            term.clear()
            term.setCursorPos(1, 1)
            print("Radio off.")
            return
        elseif key == keys.right then
            volume = math.min(3.0, volume + 0.2)
            drawUI()
        elseif key == keys.left then
            volume = math.max(0.0, volume - 0.2)
            drawUI()
        end
    end
end

-- Main
term.clear()
term.setCursorPos(1, 1)
term.setTextColor(C.header)
print("  " .. string.char(14) .. " Warming up tubes... " .. string.char(14))
sleep(0.5)

if not findSpeaker() then
    term.setTextColor(C.error)
    print("")
    print("  No speaker found!")
    print("  Place a Speaker next to this computer.")
    print("")
    print("  (Waiting...)")
    while not findSpeaker() do
        sleep(2)
    end
end

station = fetchStations()
if not station then
    term.setTextColor(C.error)
    print("")
    print("  Cannot connect to radio server!")
    print("  Is it running at " .. SERVER .. "?")
    return
end

drawUI()
parallel.waitForAny(audioLoop, metadataLoop, inputLoop)
