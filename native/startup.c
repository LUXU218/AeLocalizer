/* Wait for an After Effects editor window on the current desktop at login. */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

static BOOL editor_found;

static BOOL is_editor(HWND window) {
    WCHAR cls[128], image[32768];
    DWORD pid, length = sizeof(image) / sizeof(*image);
    if (!IsWindowVisible(window) || GetAncestor(window, GA_ROOT) != window ||
        !GetClassNameW(window, cls, 128) || wcsncmp(cls, L"AE_CApplication_", 16) != 0)
        return FALSE;
    GetWindowThreadProcessId(window, &pid);
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return FALSE;
    BOOL found = FALSE;
    if (QueryFullProcessImageNameW(process, 0, image, &length)) {
        const WCHAR *name = wcsrchr(image, L'\\');
        found = name && _wcsicmp(name + 1, L"AfterFX.exe") == 0;
    }
    CloseHandle(process);
    return found;
}

static BOOL CALLBACK check_window(HWND window, LPARAM unused) {
    (void)unused;
    if (is_editor(window)) editor_found = TRUE;
    return !editor_found;
}

static void CALLBACK shown(HWINEVENTHOOK hook, DWORD event, HWND window, LONG object,
                           LONG child, DWORD thread, DWORD time) {
    (void)hook; (void)event; (void)thread; (void)time;
    if (object == OBJID_WINDOW && child == CHILDID_SELF && is_editor(window))
        editor_found = TRUE;
}

static int launch_product(const WCHAR *scope) {
    WCHAR mutex_name[96], path[32768];
    swprintf(mutex_name, 96, L"Local\\AeLocalizer.UI.%ls", scope);
    HANDLE ui = OpenMutexW(SYNCHRONIZE, FALSE, mutex_name);
    if (ui) { CloseHandle(ui); return 0; }
    if (GetLastError() != ERROR_FILE_NOT_FOUND) return 1;
    DWORD length = GetModuleFileNameW(NULL, path, 32768);
    if (!length || length >= 32768) return 1;
    WCHAR *tail = wcsrchr(path, L'\\');
    if (!tail || (size_t)(tail - path) + 1 + sizeof(L"AeLocalizer.exe") / sizeof(WCHAR) > 32768)
        return 1;
    wcscpy(tail + 1, L"AeLocalizer.exe");
    WCHAR command[] = L"AeLocalizer.exe --from-ae-startup";
    STARTUPINFOW start = { .cb = sizeof(start) };
    PROCESS_INFORMATION child;
    if (!CreateProcessW(path, command, NULL, NULL, FALSE, 0, NULL, NULL, &start, &child))
        return 1;
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    return 0;
}

static int standby(const WCHAR *scope) {
    WCHAR name[96];
    swprintf(name, 96, L"Local\\AeLocalizer.Standby.%ls", scope);
    HANDLE cancel = CreateEventW(NULL, TRUE, FALSE, name);
    if (!cancel) return 1;
    if (GetLastError() == ERROR_ALREADY_EXISTS) { CloseHandle(cancel); return 0; }

    /* Register first so a window appearing during the initial enumeration is caught. */
    HWINEVENTHOOK hook = SetWinEventHook(EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, NULL, shown,
                                         0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS);
    int result = 1;
    if (hook) {
        MSG msg;
        EnumWindows(check_window, 0);
        for (;;) {
            if (WaitForSingleObject(cancel, 0) == WAIT_OBJECT_0) { result = 0; break; }
            if (editor_found) { result = launch_product(scope); break; }
            DWORD ready = MsgWaitForMultipleObjects(1, &cancel, FALSE, INFINITE, QS_ALLINPUT);
            if (ready == WAIT_OBJECT_0) { result = 0; break; }
            if (ready != WAIT_OBJECT_0 + 1) break;
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
        UnhookWinEvent(hook);
    }
    CloseHandle(cancel);
    return result;
}

int WINAPI wWinMain(HINSTANCE app, HINSTANCE previous, PWSTR line, int show) {
    (void)app; (void)previous; (void)line; (void)show;
    int argc, result = 2;
    WCHAR **args = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (args && argc == 3 && wcscmp(args[1], L"--wait-for-ae") == 0 &&
        wcslen(args[2]) == 32 && wcsspn(args[2], L"0123456789abcdef") == 32)
        result = standby(args[2]);
    LocalFree(args);
    return result;
}
